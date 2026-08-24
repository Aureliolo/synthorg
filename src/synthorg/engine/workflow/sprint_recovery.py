# module-kind: service
"""Pick up the sprints a stopped process left behind.

A sprint's lifecycle is driven by task-completion events. That is an edge,
and an edge does not survive the process that took it. The completion
itself is durable the moment ``complete_task_if`` commits, but the hop it
should trigger runs afterwards in a background task, so a process that dies
in between leaves a sprint whose backlog is fully delivered, whose review
never opened, and for which no completion event remains to fire again. It
sits ACTIVE for ever and the board goes on showing work in flight with
nothing behind it.

Three more routes reach the same place, none of them exotic: the spawned
tail hits a transient store error and the observer's best-effort handler
logs and swallows it; a graceful shutdown's drain times out mid-walk,
leaving a sprint at RETROSPECTIVE that nothing else moves; or the
activation that follows a sprint's creation loses its compare-and-set,
leaving a PLANNING sprint nothing activates -- which, under the partial
unique index admitting one non-completed sprint per scope, blocks that
scope permanently.

So this asks the question on a cadence instead, which is the same shape
:class:`RunRecoveryReconciler` uses for plans: boot is the first pass, and
every later pass is the same idempotent question with a different label.

Every non-terminal status gets an answer here, because a status this module
does not name is a status nothing is watching, which is the defect rather
than a gap in it. What this sweep will not do is invent a delivery: it
writes lifecycle hops and nothing else, so the worst a wrong answer can do
is advance a sprint that was already finished.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import MAX_PAGE_SIZE
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow._sprint_ops import log_sprint_transition
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_tail import advance_tail
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow import (
    SPRINT_RESUMED,
    SPRINT_TAIL_SWEEP_COMPLETE,
    SPRINT_TAIL_SWEEP_FAILED,
    SPRINT_TAIL_SWEEP_STARTED,
    SPRINT_TRANSITION_LOST,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec, SprintRepository

logger = get_logger(__name__)


@runtime_checkable
class SprintsActiveProbe(Protocol):
    """Answers whether sprint machinery applies to this org right now.

    A protocol rather than the service itself, so the sweep depends on the
    one question it actually asks instead of on everything
    ``SprintService`` can do.
    """

    async def __call__(self) -> bool:
        """Return whether sprints are enabled for an ``agile_kanban`` org."""
        ...


class SprintRecoveryReport(BaseModel):
    """What one sweep found and did."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    examined: int = Field(ge=0, description="Non-terminal sprints inspected")
    activated: int = Field(
        default=0, ge=0, description="PLANNING sprints whose activation was re-driven"
    )
    advanced: int = Field(
        default=0, ge=0, description="Sprints moved along the delivered tail"
    )
    unchanged: int = Field(
        default=0, ge=0, description="Sprints correctly left where they were"
    )
    failed: int = Field(default=0, ge=0, description="Sprints this pass could not read")


class SprintRecoveryReconciler:
    """Asks, for every unfinished sprint, whether anything is still moving it.

    Args:
        sprints: The durable sprint store; every write here is one of its
            compare-and-sets, so a pass that races the live observer loses
            harmlessly.
        sprints_active: Whether sprint machinery applies to this org at
            all. Re-asked per pass rather than captured, so an operator
            turning sprints off stops the sweep without a restart, and one
            that turns them on does not have to wait for one.
        clock: Supplies the ``end_date`` stamped on completion.
    """

    __slots__ = ("_clock", "_sprints", "_sprints_active")

    def __init__(
        self,
        *,
        sprints: SprintRepository,
        sprints_active: SprintsActiveProbe,
        clock: Clock | None = None,
    ) -> None:
        self._sprints = sprints
        self._sprints_active = sprints_active
        self._clock = clock or SystemClock()

    async def reconcile(self, *, trigger: str) -> SprintRecoveryReport:
        """Run one pass over every unfinished sprint.

        Idempotent: every write is a compare-and-set against the status
        this pass read, so a sprint the live observer is already advancing
        is left to it.

        Args:
            trigger: What caused this pass, for the log (``boot`` or
                ``periodic``).

        Returns:
            What the pass found and did. An org that does not run sprints
            reports an empty pass rather than nothing, so "the sweep ran
            and there was nothing to do" is distinguishable in the log
            from "the sweep did not run".
        """
        logger.info(SPRINT_TAIL_SWEEP_STARTED, trigger=trigger)
        if not await self._sprints_active():
            report = SprintRecoveryReport(examined=0)
            logger.info(
                SPRINT_TAIL_SWEEP_COMPLETE,
                trigger=trigger,
                note="sprints_not_active",
                **report.model_dump(),
            )
            return report
        sprints = await self._unfinished_sprints()
        activated = advanced = unchanged = failed = 0
        for sprint in sprints:
            try:
                moved = await self._reconcile_one(sprint)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- one unreadable sprint must not
                # stop the sweep; every other stranded sprint still needs
                # picking up, and the next pass re-asks about this one
                reraise_critical(exc)
                failed += 1
                logger.warning(
                    SPRINT_TAIL_SWEEP_FAILED,
                    sprint_id=sprint.id,
                    status=sprint.status.value,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            if moved is None:
                unchanged += 1
            elif moved is SprintStatus.ACTIVE:
                activated += 1
            else:
                advanced += 1
        report = SprintRecoveryReport(
            examined=len(sprints),
            activated=activated,
            advanced=advanced,
            unchanged=unchanged,
            failed=failed,
        )
        logger.info(SPRINT_TAIL_SWEEP_COMPLETE, trigger=trigger, **report.model_dump())
        return report

    async def _reconcile_one(self, sprint: Sprint) -> SprintStatus | None:
        """Move *sprint* if anything is owed to it.

        The status decides which question is asked, and every non-terminal
        one is named. ``COMPLETED`` never reaches here.

        Returns:
            The status the sprint reached, or ``None`` when it was
            correctly left where it was.

        Raises:
            AssertionError: Never at runtime; the match is exhaustive over
                a closed enum and the terminal arm is filtered upstream.
        """
        if sprint.status is SprintStatus.PLANNING:
            return await self._activate_if_stranded(sprint)
        return await self._advance_if_delivered(sprint)

    async def _activate_if_stranded(self, sprint: Sprint) -> SprintStatus | None:
        """Re-drive the activation a lost compare-and-set left undone.

        Only for a sprint that already has a backlog. An empty PLANNING
        sprint is the REST ``create_sprint`` product waiting on an
        operator's ``add_task``, and activating it would both take a
        decision that is theirs and start a sprint that is instantly
        delivered by the empty-backlog rule.

        Returns:
            ``SprintStatus.ACTIVE`` when this pass activated it, else
            ``None``.
        """
        if not sprint.task_ids:
            return None
        started = sprint.with_transition(
            SprintStatus.ACTIVE, start_date=self._clock.now().isoformat()
        )
        if not await self._sprints.transition_if(
            NotBlankStr(sprint.id),
            SprintStatus.PLANNING,
            SprintStatus.ACTIVE,
            start_date=started.start_date,
        ):
            logger.debug(
                SPRINT_TRANSITION_LOST,
                sprint_id=sprint.id,
                from_status=SprintStatus.PLANNING.value,
                to_status=SprintStatus.ACTIVE.value,
                note="recovery_activation",
            )
            return None
        log_sprint_transition(started, SprintStatus.PLANNING)
        logger.info(
            SPRINT_RESUMED,
            sprint_id=sprint.id,
            from_status=SprintStatus.PLANNING.value,
            to_status=SprintStatus.ACTIVE.value,
            note="activation_never_landed",
        )
        return SprintStatus.ACTIVE

    async def _advance_if_delivered(self, sprint: Sprint) -> SprintStatus | None:
        """Walk a delivered ACTIVE / IN_REVIEW / RETROSPECTIVE sprint onward.

        RETROSPECTIVE needs its own hop because the shared tail walk starts
        at IN_REVIEW: a sprint stopped between the two hops of
        ``finalize_if_delivered`` is exactly the shape a drain timeout
        leaves, and nothing else in the product moves that state.

        Returns:
            The status the sprint reached, or ``None`` when it was left
            alone (work genuinely outstanding, or another writer got
            there first).
        """
        if sprint.status is SprintStatus.RETROSPECTIVE:
            return await self._complete_retrospective(sprint)
        moved = await advance_tail(sprint, sprints=self._sprints, clock=self._clock)
        if moved.status is sprint.status:
            return None
        logger.info(
            SPRINT_RESUMED,
            sprint_id=sprint.id,
            from_status=sprint.status.value,
            to_status=moved.status.value,
            note="tail_never_ran",
        )
        return moved.status

    async def _complete_retrospective(self, sprint: Sprint) -> SprintStatus | None:
        """Close a sprint left in its retrospective by a stopped process.

        Returns:
            ``SprintStatus.COMPLETED`` when this pass closed it, else
            ``None``.
        """
        completed = sprint.with_transition(
            SprintStatus.COMPLETED, end_date=self._clock.now().isoformat()
        )
        if not await self._sprints.transition_if(
            NotBlankStr(sprint.id),
            SprintStatus.RETROSPECTIVE,
            SprintStatus.COMPLETED,
            end_date=completed.end_date,
        ):
            logger.debug(
                SPRINT_TRANSITION_LOST,
                sprint_id=sprint.id,
                from_status=SprintStatus.RETROSPECTIVE.value,
                to_status=SprintStatus.COMPLETED.value,
                note="recovery_retro_to_completed",
            )
            return None
        log_sprint_transition(completed, SprintStatus.RETROSPECTIVE)
        logger.info(
            SPRINT_RESUMED,
            sprint_id=sprint.id,
            from_status=SprintStatus.RETROSPECTIVE.value,
            to_status=SprintStatus.COMPLETED.value,
            note="tail_never_ran",
        )
        return SprintStatus.COMPLETED

    async def _unfinished_sprints(self) -> tuple[Sprint, ...]:
        """Read every sprint that has not completed.

        Queried per status rather than filtered from one unfiltered page,
        so a deployment with a long completed history cannot push the open
        sprints off the end of the page and out of the sweep's sight.

        Returns:
            The non-terminal sprints, in no significant order.
        """
        collected: list[Sprint] = []
        for status in SprintStatus:
            if status is SprintStatus.COMPLETED:
                continue
            collected.extend(
                await self._sprints.query(
                    SprintFilterSpec(status=status), limit=MAX_PAGE_SIZE
                )
            )
        return tuple(collected)


__all__ = ["SprintRecoveryReconciler", "SprintRecoveryReport"]
