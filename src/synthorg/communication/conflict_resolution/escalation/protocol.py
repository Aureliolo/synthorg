"""Protocols for the escalation queue backend and decision processor."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.models import (  # noqa: TC001
    Conflict,
    ConflictResolution,
    DissentRecord,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

_DEFAULT_LIMIT = 50
_DEFAULT_OFFSET = 0


@runtime_checkable
class EscalationQueueStore(Protocol):
    """Persistence contract for the human escalation queue.

    Implementations persist escalations across process restarts.  All
    methods are async to accommodate DB-backed adapters without forcing
    the in-memory default to fake blocking I/O.
    """

    async def create(self, escalation: Escalation) -> None:
        """Persist a new PENDING escalation.

        Args:
            escalation: The row to insert.  Must have ``status == PENDING``.

        Raises:
            ValueError: ``escalation.id`` already exists.
        """
        ...

    async def get(self, escalation_id: NotBlankStr) -> Escalation | None:
        """Return the escalation by ID, or ``None`` if missing."""
        ...

    async def list_items(
        self,
        *,
        status: EscalationStatus | None = EscalationStatus.PENDING,
        limit: int = _DEFAULT_LIMIT,
        offset: int = _DEFAULT_OFFSET,
    ) -> tuple[tuple[Escalation, ...], int]:
        """Page over escalations, oldest-first by ``created_at``.

        Args:
            status: Filter by status (``None`` = all statuses).
            limit: Maximum rows to return.
            offset: Number of rows to skip.

        Returns:
            Tuple ``(page, total)`` where ``total`` is the unpaginated
            row count matching ``status``.
        """
        ...

    async def apply_decision(
        self,
        escalation_id: NotBlankStr,
        *,
        decision: EscalationDecision,
        decided_by: NotBlankStr,
    ) -> Escalation:
        """Transition a PENDING escalation to DECIDED with ``decision``.

        Args:
            escalation_id: Target escalation.
            decision: Operator decision payload.
            decided_by: Operator identifier (``"human:<operator_id>"``).

        Returns:
            The updated :class:`Escalation`.

        Raises:
            KeyError: ``escalation_id`` does not exist.
            ValueError: escalation is not PENDING (already decided,
                expired, or cancelled).
        """
        ...

    async def cancel(
        self,
        escalation_id: NotBlankStr,
        *,
        cancelled_by: NotBlankStr,
    ) -> Escalation:
        """Transition a PENDING escalation to CANCELLED."""
        ...

    async def mark_expired(self, now_iso: str) -> tuple[str, ...]:
        """Transition any PENDING row with ``expires_at <= now`` to EXPIRED.

        Args:
            now_iso: Current timestamp in ISO 8601 UTC -- passed in so
                tests can pin time deterministically.

        Returns:
            IDs of escalations that were transitioned.
        """
        ...

    async def close(self) -> None:
        """Release any background resources."""
        ...

    def subscribe_notifications(
        self,
        channel: str,
    ) -> AbstractAsyncContextManager[AsyncIterator[str]]:
        """Subscribe to backend-native notifications on *channel*.

        Postgres implementations use LISTEN/NOTIFY with a dedicated
        pool connection. Single-process backends (SQLite/in-memory)
        return an iterator that blocks on cancellation without
        yielding -- correct for deployments without cross-instance
        signalling.

        Use as:

            async with repo.subscribe_notifications("my-channel") as gen:
                async for payload in gen:
                    await handle(payload)

        Args:
            channel: Channel identifier (backend-specific, validated
                by the caller; Postgres requires a safe SQL
                identifier).

        Yields:
            An async iterator of payload strings. The iterator
            terminates when the async context exits.
        """
        ...


@runtime_checkable
class CrossInstanceNotifyCapableStore(Protocol):
    """Capability marker: the store delivers real cross-instance notifications.

    Distinguishes the Postgres queue store (true LISTEN/NOTIFY plumbing)
    from the SQLite / in-memory stores whose ``subscribe_notifications``
    returns a stream that blocks on cancellation without yielding -- the
    correct shape for single-process deployments where there is no
    cross-worker concern.

    Stores opt in by declaring the
    ``supports_cross_instance_notify`` class attribute (typically as a
    ``ClassVar[bool] = True``); the escalation factory checks the
    capability via :func:`isinstance` so it never has to import any
    concrete repository class to decide whether to wire a real notify
    subscriber. Stores that lack the attribute fall through to the
    no-op subscriber automatically.
    """

    supports_cross_instance_notify: bool


@runtime_checkable
class DecisionProcessor(Protocol):
    """Converts an operator decision into a :class:`ConflictResolution`.

    Strategies differ in which decision shapes they accept:

    - :class:`WinnerSelectProcessor` (default) accepts only
      ``WinnerDecision`` -- safest surface.
    - :class:`HybridDecisionProcessor` additionally accepts
      ``RejectDecision`` and produces a ``REJECTED_BY_HUMAN`` outcome.
    """

    def process(
        self,
        conflict: Conflict,
        decision: EscalationDecision,
        *,
        decided_by: NotBlankStr,
    ) -> ConflictResolution:
        """Build a :class:`ConflictResolution` from a decision.

        Raises:
            ValueError: the decision shape is not accepted by this
                strategy, or the decision references an agent outside
                the conflict.
        """
        ...

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records for overruled positions."""
        ...
