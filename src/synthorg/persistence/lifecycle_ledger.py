# module-kind: code
"""The one way a plan or project status change becomes a durable row.

Both audited status writers (the plan service and the project writer) go
through here rather than each assembling a :class:`LifecycleTransition`
of its own, so the ledger has one shape and one failure policy.

The append runs AFTER the status write has committed, so a ledger failure
cannot be raised at the caller: the move already happened, and reporting
it as failed would be a lie the caller would act on. It is logged at ERROR
with every field the row would have carried, so the record is
reconstructible from the log rather than lost.
"""

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_transition import (
    LifecycleEntityKind,
    LifecycleTransition,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.lifecycle_transition import (
    PERSISTENCE_LIFECYCLE_TRANSITION_SAVE_FAILED,
)
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionRepository,
)
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


class LifecycleLedger:
    """Records plan and project status changes in the append-only ledger.

    Args:
        repo: The transition repository, or ``None`` when no persistence
            backend is wired (a construction-phase service, a test double).
            With none, :meth:`record` is a no-op.
        clock: Time seam supplying ``occurred_at``.
    """

    __slots__ = ("_clock", "_repo")

    def __init__(
        self,
        repo: LifecycleTransitionRepository | None,
        *,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._clock = clock

    async def record(
        self,
        *,
        entity_kind: LifecycleEntityKind,
        entity_id: NotBlankStr,
        from_status: str | None,
        to_status: NotBlankStr,
        entity_version: int,
        requested_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append one transition, or do nothing when no ledger is wired."""
        if self._repo is None:
            return
        transition = LifecycleTransition(
            entity_kind=entity_kind,
            entity_id=entity_id,
            from_status=NotBlankStr(from_status) if from_status else None,
            to_status=to_status,
            requested_by=NotBlankStr(requested_by) if requested_by else None,
            reason=NotBlankStr(reason) if reason else None,
            entity_version=entity_version,
            occurred_at=self._clock.now(),
        )
        try:
            await self._repo.append(transition)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised above
            reraise_critical(exc)
            logger.error(
                PERSISTENCE_LIFECYCLE_TRANSITION_SAVE_FAILED,
                entity_kind=entity_kind.value,
                entity_id=entity_id,
                from_status=from_status,
                to_status=to_status,
                entity_version=entity_version,
                requested_by=requested_by,
                reason=reason,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


def ledger_for(persistence: PersistenceBackend, *, clock: Clock) -> LifecycleLedger:
    """Build the ledger bound to *persistence*.

    Returns:
        A ledger writing to the backend's transition store.
    """
    return LifecycleLedger(persistence.lifecycle_transitions, clock=clock)


__all__ = ["LifecycleLedger", "ledger_for"]
