# module-kind: code
"""The one way a plan or project status change becomes a durable row.

Both audited status writers (the plan service and the project writer) go
through here rather than each assembling a :class:`LifecycleTransition`
of its own, so the ledger has one shape and one failure policy.

Each entity kind has its own entry point, typed to that kind's status enum,
because the row's ``entity_kind`` and its statuses have to agree and a single
string-taking method cannot enforce that: a PLAN row could carry a
``ProjectStatus`` and nothing would notice until somebody read the ledger
looking for how a plan reached COMPLETED.

The append runs AFTER the status write has committed, so a ledger failure
cannot be raised at the caller: the move already happened, and reporting it
as failed would be a lie the caller would act on. Instead a transient failure
is retried with bounded backoff, and what survives that is logged at ERROR
with every field of the row so it is reconstructible from the log. A single
lost row is a gap; a run of them means the ledger is not recording at all,
which is the failure this module exists to prevent, so past
:data:`_FAILURE_ESCALATION_STREAK` the log says so and recovery is announced.
"""

from typing import Final
from uuid import UUID

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_transition import (
    LifecycleEntityKind,
    LifecycleTransition,
)
from synthorg.core.persistence_errors import PersistenceError
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.lifecycle_transition import (
    PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_FAILED,
    PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_RECOVERED,
    PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_RETRIED,
    PERSISTENCE_LIFECYCLE_TRANSITION_APPENDED,
)
from synthorg.persistence.lifecycle_transition_protocol import (
    LifecycleTransitionRepository,
)
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

#: Attempts (including the first) for one ledger append. Small: the write is
#: a single-row insert on the request's tail, and a storage problem that
#: outlasts three quick tries is not going to be fixed by a fourth.
_APPEND_MAX_ATTEMPTS: Final[int] = 3

#: Backoff bounds. Deliberately sub-second: the caller has already committed
#: its status write and is holding nothing, but the ledger must not become a
#: latency source on the path that just moved a plan.
_APPEND_BASE_DELAY_SECONDS: Final[float] = 0.05
_APPEND_DELAY_CAP_SECONDS: Final[float] = 0.4

#: Consecutive failed appends after which the log stops calling it a blip.
_FAILURE_ESCALATION_STREAK: Final[int] = 3

#: Cap on the operator-authored ``reason`` copied into the lost-row log line.
#: The field is free text with no length bound of its own, and this line fires
#: once per lost row: a ledger outage would otherwise copy unbounded operator
#: prose into log retention, once per transition, for as long as it lasts.
#: Enough to identify the row, not enough to be a transport for its contents.
_MAX_LOGGED_REASON_CHARS: Final[int] = 200


def _is_retryable(exc: Exception) -> bool:
    """Whether *exc* is worth another append attempt.

    Args:
        exc: The exception the append raised.

    Returns:
        ``True`` for a persistence error the layer itself marks transient
        (a dropped connection, a deadlock). A malformed row or a constraint
        violation reproduces on every attempt, so it is not retried.
    """
    return isinstance(exc, PersistenceError) and exc.is_retryable


class LifecycleLedger:
    """Records plan and project status changes in the append-only ledger.

    Args:
        repo: The transition repository. Required rather than optional: a
            ledger that silently skips its append is indistinguishable from
            one that recorded, and the audited status writers would then
            report a move nothing durably witnessed.
        clock: Time seam supplying ``occurred_at`` and the retry backoff.
    """

    __slots__ = ("_clock", "_consecutive_failures", "_repo", "_retry")

    def __init__(
        self,
        repo: LifecycleTransitionRepository,
        *,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._consecutive_failures = 0
        self._retry = GeneralRetryHandler(
            retryable=_is_retryable,
            max_attempts=_APPEND_MAX_ATTEMPTS,
            base=_APPEND_BASE_DELAY_SECONDS,
            cap=_APPEND_DELAY_CAP_SECONDS,
            event=PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_RETRIED,
            clock=clock,
        )

    @property
    def consecutive_failures(self) -> int:
        """How many appends have failed back to back.

        Returns:
            The current failure streak; ``0`` once a row lands.
        """
        return self._consecutive_failures

    async def record_plan(
        self,
        *,
        plan_id: UUID,
        from_status: PlanStatus | None,
        to_status: PlanStatus,
        entity_version: int,
        requested_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append one plan transition.

        Args:
            plan_id: The plan that moved.
            from_status: The status it left, or ``None`` when the plan is
                being born and there is no prior status.
            to_status: The status it reached.
            entity_version: The plan's version after the move.
            requested_by: Who asked, when a caller knows.
            reason: Why, when a caller has one.
        """
        await self._append(
            LifecycleTransition(
                entity_kind=LifecycleEntityKind.PLAN,
                entity_id=NotBlankStr(str(plan_id)),
                from_status=(
                    NotBlankStr(from_status.value) if from_status is not None else None
                ),
                to_status=NotBlankStr(to_status.value),
                requested_by=NotBlankStr(requested_by) if requested_by else None,
                reason=NotBlankStr(reason) if reason else None,
                entity_version=entity_version,
                occurred_at=self._clock.now(),
            )
        )

    async def record_project(
        self,
        *,
        project_id: UUID,
        from_status: ProjectStatus | None,
        to_status: ProjectStatus,
        entity_version: int,
        requested_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Append one project transition.

        Args:
            project_id: The project that moved.
            from_status: The status it left, or ``None`` for a first
                observed status.
            to_status: The status it reached.
            entity_version: The project's version after the move.
            requested_by: Who asked, when a caller knows.
            reason: Why, when a caller has one.
        """
        await self._append(
            LifecycleTransition(
                entity_kind=LifecycleEntityKind.PROJECT,
                entity_id=NotBlankStr(str(project_id)),
                from_status=(
                    NotBlankStr(from_status.value) if from_status is not None else None
                ),
                to_status=NotBlankStr(to_status.value),
                requested_by=NotBlankStr(requested_by) if requested_by else None,
                reason=NotBlankStr(reason) if reason else None,
                entity_version=entity_version,
                occurred_at=self._clock.now(),
            )
        )

    async def _append(self, transition: LifecycleTransition) -> None:
        """Write one row, retrying a transient failure.

        Args:
            transition: The row to append.
        """
        repo = self._repo
        try:
            await self._retry.execute(
                lambda: repo.append(transition),
                entity_kind=transition.entity_kind.value,
                entity_id=transition.entity_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the status write already committed;
            # raising here would report a move that happened as failed.
            reraise_critical(exc)
            self._note_failure(transition, exc)
            return
        # Emitted here rather than in either repository: the persistence
        # boundary keeps successful-mutation logging out of repository
        # methods, and this is the one layer both backends pass through.
        logger.debug(
            PERSISTENCE_LIFECYCLE_TRANSITION_APPENDED,
            entity_kind=transition.entity_kind.value,
            entity_id=transition.entity_id,
            to_status=transition.to_status,
        )
        self._note_success()

    def _note_failure(self, transition: LifecycleTransition, exc: Exception) -> None:
        """Log a lost row in full, escalating once losing rows is a pattern.

        Every field of the row is logged, ``id`` and ``occurred_at``
        included, so the row can be reconstructed from the log rather than
        merely mourned in it. ``requested_by`` is named for the same reason
        the success path names it: a transition whose actor is missing is not
        reconstructible, and it is an identifier rather than content. The
        free-text ``reason`` is bounded, because it is the one field an
        operator can make arbitrarily long and this line repeats per lost row.

        Args:
            transition: The row that did not land.
            exc: What stopped it.
        """
        reason = transition.reason
        if reason is not None and len(reason) > _MAX_LOGGED_REASON_CHARS:
            reason = NotBlankStr(reason[:_MAX_LOGGED_REASON_CHARS])
        self._consecutive_failures += 1
        logger.error(
            PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_FAILED,
            transition_id=str(transition.id),
            entity_kind=transition.entity_kind.value,
            entity_id=transition.entity_id,
            from_status=transition.from_status,
            to_status=transition.to_status,
            entity_version=transition.entity_version,
            requested_by=transition.requested_by,
            reason=reason,
            reason_truncated=reason != transition.reason,
            occurred_at=transition.occurred_at.isoformat(),
            consecutive_failures=self._consecutive_failures,
            ledger_recording=self._consecutive_failures < _FAILURE_ESCALATION_STREAK,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    def _note_success(self) -> None:
        """Clear the streak, announcing recovery only if there was one."""
        if self._consecutive_failures >= _FAILURE_ESCALATION_STREAK:
            logger.info(
                PERSISTENCE_LIFECYCLE_TRANSITION_APPEND_RECOVERED,
                lost_rows=self._consecutive_failures,
            )
        self._consecutive_failures = 0


def ledger_for(persistence: PersistenceBackend, *, clock: Clock) -> LifecycleLedger:
    """Build the ledger bound to *persistence*.

    Returns:
        A ledger writing to the backend's transition store.
    """
    return LifecycleLedger(persistence.lifecycle_transitions, clock=clock)


__all__ = ["LifecycleLedger", "ledger_for"]
