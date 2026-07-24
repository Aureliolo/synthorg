# module-kind: service
"""The INTEGRATE stage: assemble the verified pieces into one running whole.

Every plan item passing its own review gate proves each piece works. It does
not prove they work together, and an initiative that hands back a pile of
individually-verified parts has not delivered anything. This stage is the
missing step: one accountable job that assembles the pieces and runs the whole
end to end.

**It is an ordinary task on purpose.** Minting it into the normal work pipeline
means it inherits the entire existing verification chain with no second oracle
written here: the review gate runs ``run_completion_gates``, so the build/test
oracle reads its ``CodeExecutionRecord`` rows and refuses an unverified or
failing build, the completion-oracle peer review fails closed without a distinct
reviewer, and output policy, red team, and vision all apply. A bespoke
"integration checker" would have been a second, weaker gate.

Two shape decisions carry weight. The task is forced ``LEAF``: splitting an
assembly job hands the pieces back to separate agents, which is the state this
stage exists to end. And it carries ``plan_id`` but no ``plan_item_id``: it
belongs to the initiative without implementing any plan item, so every
derivation over plan items ignores it and it cannot distort the rollup that
opened this stage.

Minting is idempotent by construction: each attempt's task id is derived from
the plan id and the attempt index, so a re-fired edge finds the existing row
and stops.
"""

from typing import Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Complexity, Stakes, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.initiative.integrate_brief import (
    INTEGRATION_ARTIFACTS,
    build_integration_brief,
    integration_title,
)
from synthorg.engine.initiative.stage_runner import StageRunner
from synthorg.engine.initiative.tail_stages import (
    INTEGRATION_ACTOR,
    integration_task_id,
    integration_task_uuid,
    is_integration_task,
)
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_INTEGRATION_DISPATCHED,
    INITIATIVE_INTEGRATION_FAILED,
    INITIATIVE_INTEGRATION_SCHEDULED,
    INITIATIVE_INTEGRATION_SKIPPED,
    INITIATIVE_INTEGRATION_STARTED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

#: Identity recorded on the integration task, re-exported so callers reading
#: provenance and callers minting the row agree by construction.
ACTOR: Final[str] = INTEGRATION_ACTOR

#: Adapter id the integration brief enters the pipeline under.
_ORIGIN: Final[str] = "initiative-tail"

#: Wall-clock ceiling on one assembly attempt, used when no resolver is wired
#: or the read fails. The pipeline runs the assembly inline, so this bounds the
#: whole attempt, not just the hand-off.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 1800.0

#: One level up from the plan's own highest-stakes item. Assembling is where a
#: mistake is most expensive: it is the first point the whole thing runs, and
#: the last point before the objective is judged.
_STAKES_LADDER: Final[tuple[Stakes, ...]] = (
    Stakes.LOW,
    Stakes.NORMAL,
    Stakes.HIGH,
    Stakes.CRITICAL,
)


def escalated_stakes(plan: Plan) -> Stakes:
    """Return the stakes the integration task runs at.

    Returns:
        One level above the plan's highest-stakes item, capped at CRITICAL.
    """
    highest = max(
        (_STAKES_LADDER.index(item.stakes) for item in plan.items),
        default=_STAKES_LADDER.index(Stakes.NORMAL),
    )
    return _STAKES_LADDER[min(highest + 1, len(_STAKES_LADDER) - 1)]


class IntegrationStageService:
    """Mints and dispatches the one assembly job an initiative needs.

    Args:
        persistence: Backend supplying the plan and task repositories.
        task_engine: Reads the objective task the integration task hangs off.
        pipeline: The work spine the integration task is dispatched through,
            so it runs under the same routing, budgets, and review gate as
            every other task.
        config_resolver: Live settings source, re-read per fire.
        clock: Clock seam seeding the background-task drain deadline.
    """

    __slots__ = (
        "_config_resolver",
        "_persistence",
        "_pipeline",
        "_runner",
        "_task_engine",
    )

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        task_engine: TaskEngine,
        pipeline: WorkPipeline,
        config_resolver: ConfigResolver | None = None,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._task_engine = task_engine
        self._pipeline = pipeline
        self._config_resolver = config_resolver
        self._runner = StageRunner(
            owner="initiative.integrate",
            clock=clock,
            skipped_event=INITIATIVE_INTEGRATION_SKIPPED,
            failed_event=INITIATIVE_INTEGRATION_FAILED,
        )

    def schedule(self, *, plan: Plan, attempt: int = 0) -> None:
        """Schedule the assembly job for a plan sitting in INTEGRATING.

        Returns immediately; the work runs detached on a tracked task so the
        rollup observer is never blocked. Safe to call from a best-effort
        observer: it never raises.
        """
        started = self._runner.start(
            key=str(plan.id),
            work=self._run(plan, attempt),
            deadline=self._timeout_seconds,
            fallback_seconds=_DEFAULT_TIMEOUT_SECONDS,
            fields={"plan_id": str(plan.id), "attempt": attempt},
        )
        if started:
            logger.info(
                INITIATIVE_INTEGRATION_SCHEDULED,
                plan_id=str(plan.id),
                attempt=attempt,
            )

    async def drain(self, *, timeout_sec: float) -> None:
        """Wait for outstanding integration dispatches at shutdown."""
        await self._runner.drain(timeout_sec=timeout_sec)

    async def settle(self, *, timeout_sec: float) -> None:
        """Wait for in-flight dispatches without closing the stage."""
        await self._runner.settle(timeout_sec=timeout_sec)

    async def _run(self, plan: Plan, attempt: int) -> None:
        """Mint the assembly job if it is still needed, then dispatch it."""
        fresh = await self._persistence.plans.get(NotBlankStr(str(plan.id)))
        if fresh is None or fresh.status is not PlanStatus.INTEGRATING:
            logger.debug(
                INITIATIVE_INTEGRATION_SKIPPED,
                plan_id=str(plan.id),
                reason="no_longer_integrating",
                status=fresh.status.value if fresh else None,
            )
            return
        objective = await self._task_engine.get_task(fresh.parent_task_id)
        if objective is None:
            logger.warning(
                INITIATIVE_INTEGRATION_FAILED,
                plan_id=str(fresh.id),
                reason="objective_task_missing",
            )
            return
        # Idempotency rests on the deterministic ``integration_task_id`` plus
        # this get-then-resume: a redelivered observer event finds the minted
        # row and re-hands it rather than dispatching a second job. This is
        # sufficient because a single in-process ``TaskEngine`` owns the plan's
        # writes; there is no second concurrent writer of this row to race the
        # insert against. A move to a multi-writer, shared-database topology
        # would need an atomic insert-only claim here instead.
        existing = await self._persistence.tasks.get(
            integration_task_id(fresh, attempt)
        )
        if existing is not None:
            await self._resume(fresh, objective, existing)
            return
        logger.info(
            INITIATIVE_INTEGRATION_STARTED,
            plan_id=str(fresh.id),
            project=str(fresh.project),
            attempt=attempt,
        )
        await self._dispatch(fresh, objective, attempt)

    async def _resume(self, plan: Plan, objective: Task, existing: Task) -> None:
        """Re-hand an already-minted assembly job to the pipeline, if it stalled.

        The row is persisted before the pipeline is handed the task, and the
        pipeline runs the assembly inline, so a dispatch that died in between
        leaves a row nothing is driving. A task still at CREATED is exactly
        that case and is re-dispatchable; anything further along is either
        under way or finished, and the outcome read owns it from there.
        """
        if not is_integration_task(existing, plan):
            logger.warning(
                INITIATIVE_INTEGRATION_FAILED,
                plan_id=str(plan.id),
                task_id=str(existing.id),
                reason="task_id_occupied_by_foreign_task",
            )
            return
        if existing.status is not TaskStatus.CREATED:
            logger.debug(
                INITIATIVE_INTEGRATION_SKIPPED,
                plan_id=str(plan.id),
                task_id=str(existing.id),
                reason="already_minted",
                status=existing.status.value,
            )
            return
        logger.info(
            INITIATIVE_INTEGRATION_STARTED,
            plan_id=str(plan.id),
            project=str(plan.project),
            task_id=str(existing.id),
            note="re-dispatching an assembly job that never left created",
        )
        await self._hand_to_pipeline(plan, objective, existing)

    async def _dispatch(self, plan: Plan, objective: Task, attempt: int) -> None:
        """Persist the assembly job and run it through the work spine."""
        brief = build_integration_brief(plan)
        task = Task(
            id=integration_task_uuid(plan, attempt),
            title=NotBlankStr(integration_title(plan)),
            description=NotBlankStr(brief),
            type=objective.type,
            priority=objective.priority,
            project=NotBlankStr(str(plan.project)),
            plan_id=plan.id,
            # No plan_item_id: this implements no plan item, so every
            # derivation over items must ignore it.
            created_by=NotBlankStr(ACTOR),
            parent_task_id=plan.parent_task_id,
            delegation_chain=objective.delegation_chain,
            acceptance_criteria=tuple(
                AcceptanceCriterion(description=criterion)
                for criterion in plan.objective_criteria
            ),
            artifacts_expected=tuple(
                expected_artifact_from_spec(NotBlankStr(spec))
                for spec in INTEGRATION_ARTIFACTS
            ),
            status=TaskStatus.CREATED,
            estimated_complexity=Complexity.COMPLEX,
            stakes=escalated_stakes(plan),
        )
        await self._persistence.tasks.save(task)
        await self._hand_to_pipeline(plan, objective, task)

    async def _hand_to_pipeline(
        self,
        plan: Plan,
        objective: Task,
        task: Task,
    ) -> None:
        """Run *task* through the work spine as a forced-LEAF objective item."""
        work_item = WorkItem(
            origin_adapter_id=NotBlankStr(_ORIGIN),
            source=WorkSource.OBJECTIVE,
            title=task.title,
            raw_intent=task.description,
            project=NotBlankStr(str(plan.project)),
            requested_by=NotBlankStr(ACTOR),
            priority=objective.priority,
            task_type=objective.type,
            estimated_complexity=Complexity.COMPLEX,
            acceptance_criteria=plan.objective_criteria,
            leaf_required=True,
        )
        logger.info(
            INITIATIVE_INTEGRATION_DISPATCHED,
            plan_id=str(plan.id),
            task_id=str(task.id),
            project=str(plan.project),
            stakes=task.stakes.value,
        )
        _ = await self._pipeline.continue_from_intake(work_item, task)

    async def _timeout_seconds(self) -> float:
        """Resolve the per-dispatch wall-clock ceiling.

        Returns:
            The ``engine.integration_stage_timeout_seconds`` value, or the
            default when no resolver is wired or the read fails.
        """
        if self._config_resolver is None:
            return _DEFAULT_TIMEOUT_SECONDS
        try:
            resolved = await self._config_resolver.get_float(
                "engine", "integration_stage_timeout_seconds"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            logger.warning(
                INITIATIVE_INTEGRATION_SKIPPED,
                key="integration_stage_timeout_seconds",
                reason="settings_read_degraded",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _DEFAULT_TIMEOUT_SECONDS
        return resolved if resolved > 0 else _DEFAULT_TIMEOUT_SECONDS
