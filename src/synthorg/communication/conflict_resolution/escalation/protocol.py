"""Protocols for the escalation queue backend and decision processor."""

from typing import TYPE_CHECKING, Final, Literal, Protocol, runtime_checkable

from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictResolution,
    DissentRecord,
)
from synthorg.core.clock import Clock
from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

_DEFAULT_LIMIT: Final[int] = 50
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
    ``supports_cross_instance_notify`` class attribute as
    ``ClassVar[Literal[True]] = True``. The annotation is ``Literal[True]``
    rather than ``bool`` so a store declaring ``= False`` is a static
    type error: the attribute is a true opt-in, not a togglable flag.
    The factory complements this static guarantee with an explicit
    runtime ``is True`` check, since ``@runtime_checkable`` only verifies
    attribute presence, not value, and ``isinstance(store, ...)`` would
    otherwise return ``True`` for stores that set the attribute to any
    value (including ``False``). Stores that lack the attribute fall
    through to the no-op subscriber automatically.
    """

    supports_cross_instance_notify: Literal[True]


@runtime_checkable
class DecisionProcessor(Protocol):
    """Converts an operator decision into a :class:`ConflictResolution`.

    Concrete implementations differ in which decision shapes they
    accept:

    - :class:`WinnerOnlyDecisionProcessor` (safest surface, the
      default) accepts only :class:`WinnerDecision`. Receiving a
      :class:`RejectDecision` raises
      :class:`EscalationDecisionShapeError`.
    - :class:`HybridDecisionProcessor` additionally accepts
      :class:`RejectDecision` and produces a ``REJECTED_BY_HUMAN``
      outcome so callers can fall back to a different strategy.
    """

    def process(
        self,
        conflict: Conflict,
        decision: EscalationDecision,
        *,
        decided_by: NotBlankStr,
        clock: Clock | None = None,
    ) -> ConflictResolution:
        """Build a :class:`ConflictResolution` from a decision.

        Args:
            conflict: The conflict being resolved.
            decision: The operator's decision shape.
            decided_by: Operator id (audit field).
            clock: Optional injectable clock; tests inject
                :class:`FakeClock` so the ``resolved_at`` timestamp is
                deterministic. Defaults to :class:`SystemClock`.

        Raises:
            EscalationDecisionShapeError: the decision shape is not
                accepted by this strategy.
            EscalationDecisionAgentError: the decision references an
                agent outside the conflict.
        """
        ...

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
        *,
        clock: Clock | None = None,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records for overruled positions.

        ``clock`` is injectable for deterministic dissent timestamps in
        tests; defaults to :class:`SystemClock`.
        """
        ...
