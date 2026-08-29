# module-kind: service
"""The SKELETON stage: write the contract as code before anything builds on it.

An approved plan says what the units are. It does not say what they are building
against, and prose cannot: a brief that describes a seam in paragraphs leaves
each unit to invent its own reading of it, and the first thing that reconciles
those readings is the assembly at the very end. This stage is the missing step:
one accountable job that commits module layout and type signatures, one pending
test per acceptance criterion, and the project's gate configuration, so a leaf's
brief becomes a signature plus a failing test it has to make pass.

**It is an ordinary task on purpose**, for exactly the reason the assembly stage
is. Minting it into the normal work pipeline means it inherits the entire
existing verification chain with no second oracle written here: the review gate
runs ``run_completion_gates``, so the build/test oracle reads its
``CodeExecutionRecord`` rows and refuses a skeleton that does not build, the
completion-oracle peer review fails closed without a distinct reviewer, and
output policy, red team and vision all apply. That review matters more here than
anywhere else in the run, because every unit below is briefed from this output:
a contract nobody read is one every leaf inherits.

Two shape decisions carry weight, and they are the assembly stage's decisions
seen from the other end. The task is forced ``LEAF``: splitting the contract
across agents is how you get two contracts. And it carries ``plan_id`` but no
``plan_item_id``, so every derivation over plan items ignores it and it cannot
distort the rollup. Provenance is what tells the two stages apart, since both
rows are alike in those two fields: the actor is the discriminator, and
:mod:`synthorg.engine.initiative.head_stages` owns it.

Minting is idempotent by construction: each attempt's task id is derived from
the plan id and the attempt index, so a re-fired edge finds the existing row and
stops.
"""

from typing import Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Complexity, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.assembly import escalated_stakes
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.initiative.head_stages import (
    SKELETON_ACTOR,
    is_skeleton_task,
    skeleton_task_uuid,
)
from synthorg.engine.initiative.skeleton_brief import (
    SKELETON_ARTIFACTS,
    build_skeleton_brief,
    skeleton_title,
)
from synthorg.engine.initiative.stage_runner import StageRunner
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_SKELETON_DISPATCHED,
    INITIATIVE_SKELETON_FAILED,
    INITIATIVE_SKELETON_SCHEDULED,
    INITIATIVE_SKELETON_SKIPPED,
    INITIATIVE_SKELETON_STARTED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

#: Identity recorded on the skeleton task, re-exported so callers reading
#: provenance and callers minting the row agree by construction.
ACTOR: Final[str] = SKELETON_ACTOR

#: Adapter id the skeleton brief enters the pipeline under.
_ORIGIN: Final[str] = "initiative-head"

#: Wall-clock ceiling on one skeleton attempt, used when no resolver is wired or
#: the read fails. The pipeline runs the job inline, so this bounds the whole
#: attempt, not just the hand-off.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 1800.0


class SkeletonStageService:
    """Mints and dispatches the one contract job an initiative needs.

    Args:
        persistence: Backend supplying the plan and task repositories.
        task_engine: Reads the objective task the skeleton task hangs off.
        pipeline: The work spine the skeleton task is dispatched through, so it
            runs under the same routing, budgets and review gate as every other
            task.
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
            owner="initiative.skeleton",
            clock=clock,
            skipped_event=INITIATIVE_SKELETON_SKIPPED,
            failed_event=INITIATIVE_SKELETON_FAILED,
        )

    def schedule(self, *, plan: Plan, attempt: int = 0) -> None:
        """Schedule the contract job for a plan sitting in SKELETON.

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
                INITIATIVE_SKELETON_SCHEDULED,
                plan_id=str(plan.id),
                attempt=attempt,
            )

    async def drain(self, *, timeout_sec: float) -> None:
        """Wait for outstanding skeleton dispatches at shutdown."""
        await self._runner.drain(timeout_sec=timeout_sec)

    async def settle(self, *, timeout_sec: float) -> None:
        """Wait for in-flight dispatches without closing the stage."""
        await self._runner.settle(timeout_sec=timeout_sec)

    async def _run(self, plan: Plan, attempt: int) -> None:
        """Mint the contract job if it is still needed, then dispatch it."""
        fresh = await self._persistence.plans.get(NotBlankStr(str(plan.id)))
        if fresh is None or fresh.status is not PlanStatus.SKELETON:
            logger.debug(
                INITIATIVE_SKELETON_SKIPPED,
                plan_id=str(plan.id),
                reason="no_longer_skeleton",
                status=fresh.status.value if fresh else None,
            )
            return
        objective = await self._task_engine.get_task(fresh.parent_task_id)
        if objective is None:
            logger.warning(
                INITIATIVE_SKELETON_FAILED,
                plan_id=str(fresh.id),
                reason="objective_task_missing",
            )
            return
        # Idempotency rests on the deterministic id plus this get-then-resume: a
        # redelivered observer event finds the minted row and re-hands it rather
        # than dispatching a second job. Sufficient because a single in-process
        # TaskEngine owns the plan's writes; a multi-writer topology would need
        # an atomic insert-only claim here instead.
        existing = await self._persistence.tasks.get(
            str(skeleton_task_uuid(fresh, attempt))
        )
        if existing is not None:
            await self._resume(fresh, objective, existing)
            return
        logger.info(
            INITIATIVE_SKELETON_STARTED,
            plan_id=str(fresh.id),
            project=str(fresh.project),
            attempt=attempt,
        )
        await self._dispatch(fresh, objective, attempt)

    async def _resume(self, plan: Plan, objective: Task, existing: Task) -> None:
        """Re-hand an already-minted contract job to the pipeline, if it stalled.

        The row is persisted before the pipeline is handed the task, and the
        pipeline runs the job inline, so a dispatch that died in between leaves
        a row nothing is driving. A task still at CREATED is exactly that case
        and is re-dispatchable; anything further along is either under way or
        finished, and the outcome read owns it from there.
        """
        if not is_skeleton_task(existing, plan):
            logger.warning(
                INITIATIVE_SKELETON_FAILED,
                plan_id=str(plan.id),
                task_id=str(existing.id),
                reason="task_id_occupied_by_foreign_task",
            )
            return
        if existing.status is not TaskStatus.CREATED:
            logger.debug(
                INITIATIVE_SKELETON_SKIPPED,
                plan_id=str(plan.id),
                task_id=str(existing.id),
                reason="already_minted",
                status=existing.status.value,
            )
            return
        logger.info(
            INITIATIVE_SKELETON_STARTED,
            plan_id=str(plan.id),
            project=str(plan.project),
            task_id=str(existing.id),
            note="re-dispatching a contract job that never left created",
        )
        await self._hand_to_pipeline(plan, objective, existing)

    async def _dispatch(self, plan: Plan, objective: Task, attempt: int) -> None:
        """Persist the contract job and run it through the work spine."""
        task = Task(
            id=skeleton_task_uuid(plan, attempt),
            title=NotBlankStr(skeleton_title(plan)),
            description=NotBlankStr(build_skeleton_brief(plan)),
            type=objective.type,
            priority=objective.priority,
            project=NotBlankStr(str(plan.project)),
            plan_id=plan.id,
            # No plan_item_id: this implements no plan item, so every derivation
            # over items must ignore it.
            created_by=NotBlankStr(ACTOR),
            parent_task_id=plan.parent_task_id,
            delegation_chain=objective.delegation_chain,
            acceptance_criteria=tuple(
                AcceptanceCriterion(description=criterion)
                for criterion in plan.objective_criteria
            ),
            artifacts_expected=tuple(
                expected_artifact_from_spec(NotBlankStr(spec))
                for spec in SKELETON_ARTIFACTS
            ),
            status=TaskStatus.CREATED,
            estimated_complexity=Complexity.COMPLEX,
            # The contract binds every unit below it, so it is judged at the
            # highest stakes any of them carry rather than at its own apparent
            # size: getting a seam wrong here is not a small mistake because it
            # is a small file.
            stakes=escalated_stakes([item.stakes for item in plan.items]),
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
            INITIATIVE_SKELETON_DISPATCHED,
            plan_id=str(plan.id),
            task_id=str(task.id),
            project=str(plan.project),
            stakes=task.stakes.value,
        )
        _ = await self._pipeline.continue_from_intake(work_item, task)

    async def _timeout_seconds(self) -> float:
        """Resolve the per-dispatch wall-clock ceiling.

        Returns:
            The ``engine.skeleton_stage_timeout_seconds`` value, or the default
            when no resolver is wired or the read fails.
        """
        if self._config_resolver is None:
            return _DEFAULT_TIMEOUT_SECONDS
        try:
            resolved = await self._config_resolver.get_float(
                "engine", "skeleton_stage_timeout_seconds"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            logger.warning(
                INITIATIVE_SKELETON_SKIPPED,
                key="skeleton_stage_timeout_seconds",
                reason="settings_read_degraded",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _DEFAULT_TIMEOUT_SECONDS
        return resolved if resolved > 0 else _DEFAULT_TIMEOUT_SECONDS
