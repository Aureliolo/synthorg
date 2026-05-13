"""In-memory stores for client simulation runtime state."""

import asyncio
from collections.abc import Mapping  # noqa: TC003 - used at module-level runtime
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.client.models import (
    ClientFeedback,
    ClientRequest,
    SimulationConfig,
    SimulationMetrics,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.client import (
    CLIENT_FEEDBACK_RECORDED,
    CLIENT_REQUEST_SUBMITTED,
    SIMULATION_RUN_CANCELLED,
    SIMULATION_RUN_COMPLETED,
    SIMULATION_RUN_FAILED,
    SIMULATION_RUN_STARTED,
    SIMULATION_RUN_UPDATE_REJECTED,
)

logger = get_logger(__name__)

SimulationRunStatus = Literal[
    "pending",
    "running",
    "completed",
    "cancelled",
    "failed",
]
_TERMINAL_STATUSES: frozenset[SimulationRunStatus] = frozenset(
    {"completed", "cancelled", "failed"},
)
_STATUS_EVENTS: Mapping[SimulationRunStatus, str] = MappingProxyType(
    {
        "running": SIMULATION_RUN_STARTED,
        "completed": SIMULATION_RUN_COMPLETED,
        "cancelled": SIMULATION_RUN_CANCELLED,
        "failed": SIMULATION_RUN_FAILED,
    },
)


class FeedbackStore:
    """Thread-safe in-memory store for :class:`ClientFeedback`.

    Indexed by ``client_id`` so ``/clients/{id}/satisfaction`` can
    return the full review history for a single client without
    scanning every entry.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._lock = asyncio.Lock()
        self._by_client: dict[str, list[ClientFeedback]] = {}

    async def record(self, feedback: ClientFeedback) -> None:
        """Append a feedback entry for its client."""
        async with self._lock:
            bucket = self._by_client.setdefault(feedback.client_id, [])
            bucket.append(feedback)
            # Capture the count inside the lock so a concurrent
            # ``record`` cannot skew the value we log.
            feedback_count = len(bucket)
        logger.info(
            CLIENT_FEEDBACK_RECORDED,
            client_id=feedback.client_id,
            feedback_count=feedback_count,
        )

    async def list_for_client(
        self,
        client_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[ClientFeedback, ...]:
        """Return feedback entries recorded for a client, optionally paginated.

        ``limit=None`` (the default) preserves the historical "return
        everything" contract for aggregation callers (e.g.
        ``get_satisfaction_history`` computes acceptance rate over the
        entire feedback set). When ``limit`` is set, ``offset`` is
        honoured and the snapshot is sliced in insertion order.
        """
        async with self._lock:
            snapshot = tuple(self._by_client.get(client_id, ()))
        if limit is None and offset == 0:
            return snapshot
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        return snapshot[offset:end]

    async def count_for_client(self, client_id: str) -> int:
        """Return the count of feedback entries recorded for ``client_id``."""
        async with self._lock:
            return len(self._by_client.get(client_id, ()))

    async def clear(self, client_id: str) -> None:
        """Remove all feedback entries for a client (no-op if absent)."""
        async with self._lock:
            self._by_client.pop(client_id, None)


class RequestStore:
    """Thread-safe in-memory store for :class:`ClientRequest`.

    Backed by a dict keyed on ``request_id`` and guarded by an
    :class:`asyncio.Lock`. Used by the API controllers to surface
    the independent request lifecycle through HTTP endpoints.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._lock = asyncio.Lock()
        self._requests: dict[str, ClientRequest] = {}

    async def save(self, request: ClientRequest) -> None:
        """Insert or replace a request by id.

        Only emits :data:`CLIENT_REQUEST_SUBMITTED` when a genuinely
        new request arrives. Replacing an existing id (e.g. a
        retry that updates state) would otherwise inflate the
        request-submitted counter in dashboards.
        """
        async with self._lock:
            is_new = request.request_id not in self._requests
            self._requests[request.request_id] = request
        if is_new:
            logger.info(
                CLIENT_REQUEST_SUBMITTED,
                request_id=request.request_id,
                client_id=request.client_id,
            )

    async def get(self, request_id: str) -> ClientRequest:
        """Return the request by id or raise ``KeyError``."""
        async with self._lock:
            if request_id not in self._requests:
                msg = f"Request {request_id!r} not found"
                raise KeyError(msg)
            return self._requests[request_id]

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[ClientRequest, ...]:
        """Return a snapshot of stored requests, optionally paginated.

        ``limit=None`` (the default) preserves the historical "return
        everything" contract. When ``limit`` is set, ``offset`` is
        honoured and the snapshot is sliced in insertion order.
        """
        async with self._lock:
            snapshot = tuple(self._requests.values())
        if limit is None and offset == 0:
            return snapshot
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        return snapshot[offset:end]

    async def count_all(self) -> int:
        """Return the count of stored requests."""
        async with self._lock:
            return len(self._requests)

    async def delete(self, request_id: str) -> None:
        """Remove a request by id (no-op if absent)."""
        async with self._lock:
            self._requests.pop(request_id, None)


class SimulationRecord(BaseModel):
    """Status record for a simulation run.

    Frozen Pydantic model. Persisted in-memory by
    :class:`SimulationStore` and exposed through the
    ``/simulations`` endpoints. Mutations go through
    ``SimulationStore.update_status`` which constructs a new record
    and rebinds it in the store -- never mutates in place.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    simulation_id: NotBlankStr = Field(description="Run identifier")
    config: SimulationConfig = Field(description="Run configuration")
    status: SimulationRunStatus = Field(default="pending")
    metrics: SimulationMetrics = Field(default_factory=SimulationMetrics)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    started_at: AwareDatetime | None = Field(default=None)
    completed_at: AwareDatetime | None = Field(default=None)
    error: str | None = Field(default=None)


class SimulationStore:
    """Thread-safe in-memory store for simulation run records."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._lock = asyncio.Lock()
        self._runs: dict[str, SimulationRecord] = {}

    async def save(self, record: SimulationRecord) -> None:
        """Insert or replace a simulation record."""
        async with self._lock:
            self._runs[record.simulation_id] = record

    async def register_if_absent(self, record: SimulationRecord) -> bool:
        """Atomically insert *record* if no entry exists for its id.

        Returns ``True`` when *record* was inserted (the caller is the
        "winner" and should spawn the runner), ``False`` when a record
        for ``record.simulation_id`` already exists (the caller should
        return HTTP 409 and let the existing runner finish).

        The check-and-insert happens under ``self._lock`` so two
        concurrent callers cannot both observe absence and proceed.
        """
        async with self._lock:
            if record.simulation_id in self._runs:
                return False
            self._runs[record.simulation_id] = record
            return True

    async def unregister(
        self,
        simulation_id: str,
        *,
        expected: SimulationRecord | None = None,
    ) -> bool:
        """Compare-and-delete the registration if it matches *expected*.

        Returns ``True`` when the entry was removed, ``False`` when no
        entry existed OR a different record now occupies the slot. Used
        by ``start_simulation`` to roll back a successful
        ``register_if_absent`` if the post-claim setup (event publish,
        runner spawn) raises -- without rollback the ``simulation_id``
        would stay claimed forever and block every retry.

        Passing ``expected`` makes the rollback safe under concurrent
        retries: if the original claim has already been replaced by a
        new run between the failure and the rollback (a fast retry
        succeeded while the loser was still tearing down), the new run
        is preserved instead of being silently deleted. ``expected=None``
        keeps the old unconditional-delete semantics for callers that
        don't have a snapshot to compare against.
        """
        async with self._lock:
            current = self._runs.get(simulation_id)
            if current is None:
                return False
            if expected is not None and current is not expected:
                return False
            del self._runs[simulation_id]
            return True

    async def get(self, simulation_id: str) -> SimulationRecord:
        """Return the record by id or raise ``KeyError``."""
        async with self._lock:
            if simulation_id not in self._runs:
                msg = f"Simulation {simulation_id!r} not found"
                raise KeyError(msg)
            return self._runs[simulation_id]

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[SimulationRecord, ...]:
        """Return a snapshot of records, optionally paginated.

        ``limit=None`` (the default) preserves the historical "return
        everything" contract. When ``limit`` is set, ``offset`` is
        honoured and the snapshot is sliced in insertion order.
        """
        async with self._lock:
            snapshot = tuple(self._runs.values())
        if limit is None and offset == 0:
            return snapshot
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        return snapshot[offset:end]

    async def count_all(self) -> int:
        """Return the count of simulation records."""
        async with self._lock:
            return len(self._runs)

    async def update_status(
        self,
        simulation_id: str,
        *,
        status: SimulationRunStatus,
        metrics: SimulationMetrics | None = None,
        progress: float | None = None,
        error: str | None = None,
    ) -> SimulationRecord:
        """Replace the stored record with an updated copy.

        Constructs a new frozen :class:`SimulationRecord` via
        ``model_copy`` rather than mutating in place, honoring the
        project's immutability rule.
        """
        async with self._lock:
            if simulation_id not in self._runs:
                logger.warning(
                    SIMULATION_RUN_UPDATE_REJECTED,
                    reason="not_found",
                    simulation_id=simulation_id,
                    requested_status=status,
                    current_status="missing",
                )
                msg = f"Simulation {simulation_id!r} not found"
                raise KeyError(msg)
            existing = self._runs[simulation_id]
            updates: dict[str, object] = {"status": status}
            if metrics is not None:
                updates["metrics"] = metrics
            if progress is not None:
                updates["progress"] = progress
            if error is not None:
                updates["error"] = error
            # Reject only real *transitions* out of a terminal status.
            # Idempotent same-status writes (``completed -> completed``
            # with a late metrics / error payload) stay harmless and
            # fall through to the no-op event guard below.
            if existing.status in _TERMINAL_STATUSES and status != existing.status:
                logger.warning(
                    SIMULATION_RUN_UPDATE_REJECTED,
                    reason="terminal_state_transition_rejected",
                    simulation_id=simulation_id,
                    requested_status=status,
                    current_status=existing.status,
                    progress=existing.progress,
                )
                msg = (
                    f"Simulation {simulation_id!r} already in "
                    f"terminal status {existing.status!r}"
                )
                raise ValueError(msg)
            if status == "running" and existing.started_at is None:
                updates["started_at"] = datetime.now(UTC)
            if status in _TERMINAL_STATUSES and existing.completed_at is None:
                updates["completed_at"] = datetime.now(UTC)
            updated = existing.model_copy(update=updates)
            self._runs[simulation_id] = updated
        event = _STATUS_EVENTS.get(status)
        # Only emit a transition event when the status actually
        # changed; logging on every no-op update (e.g. repeated
        # ``running -> running`` progress writes) would inflate
        # ``simulation_run_started`` counts.
        if event is not None and existing.status != status:
            logger.info(
                event,
                simulation_id=simulation_id,
                previous_status=existing.status,
                new_status=status,
                progress=updated.progress,
            )
        return updated
