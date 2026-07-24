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

Minting is idempotent by construction: the task id is derived from the plan id,
so a re-fired edge finds the existing row and stops.
"""

import asyncio
from typing import Final
from uuid import NAMESPACE_OID, UUID, uuid5

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
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.initiative import (
    INITIATIVE_INTEGRATION_DISPATCHED,
    INITIATIVE_INTEGRATION_FAILED,
    INITIATIVE_INTEGRATION_SKIPPED,
    INITIATIVE_INTEGRATION_STARTED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

#: Identity recorded on the integration task, so the board shows the stage
#: rather than attributing the work to whoever last touched the initiative.
ACTOR: Final[str] = "initiative-integrate"

#: Adapter id the integration brief enters the pipeline under.
_ORIGIN: Final[str] = "initiative-tail"

#: Namespace the integration task's id is derived in, so it is stable across
#: processes and restarts without colliding with any other derived id.
_TASK_NAMESPACE: Final[UUID] = uuid5(NAMESPACE_OID, "synthorg.initiative.integrate")

#: Wall-clock ceiling on minting and dispatching, used when no resolver is
#: wired or the read fails. This bounds the dispatch, not the run: the task
#: itself is governed by the pipeline's own budgets once it is under way.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 1800.0

#: One level up from the plan's own highest-stakes item. Assembling is where a
#: mistake is most expensive: it is the first point the whole thing runs, and
#: the last point before delivery.
_STAKES_LADDER: Final[tuple[Stakes, ...]] = (
    Stakes.LOW,
    Stakes.NORMAL,
    Stakes.HIGH,
    Stakes.CRITICAL,
)


def integration_task_uuid(plan: Plan) -> UUID:
    """Return the deterministic id of *plan*'s integration task.

    Derived from the plan id rather than random, so a re-fired stage edge
    resolves the same row instead of minting a second assembly job. That is
    the whole idempotency mechanism: there is no separate "already started"
    flag to keep in step with reality.

    Returns:
        The integration task's id.
    """
    return uuid5(_TASK_NAMESPACE, str(plan.id))


def integration_task_id(plan: Plan) -> str:
    """Return :func:`integration_task_uuid` in the repositories' string form.

    Returns:
        The integration task's id, as a canonical UUID string.
    """
    return str(integration_task_uuid(plan))


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
        "_clock",
        "_config_resolver",
        "_inflight",
        "_persistence",
        "_pipeline",
        "_task_engine",
        "_tasks",
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
        self._clock = clock
        self._tasks = BackgroundTaskRegistry(owner="initiative.integrate", clock=clock)
        # Plan ids with a dispatch in flight this process. The rollup fires on
        # every recompute that reads INTEGRATING, and the persisted-row check
        # only closes the window once the row is written, so this collapses a
        # burst synchronously (checked-and-set before spawn, no await).
        self._inflight: set[str] = set()

    def schedule(self, *, plan: Plan) -> None:
        """Schedule the integration job for a plan that entered INTEGRATING.

        Returns immediately; the work runs detached on a tracked task so the
        rollup observer is never blocked. Safe to call from a best-effort
        observer: it never raises.
        """
        plan_id = str(plan.id)
        if plan_id in self._inflight:
            logger.debug(
                INITIATIVE_INTEGRATION_SKIPPED,
                plan_id=plan_id,
                reason="already_inflight",
            )
            return
        self._inflight.add(plan_id)
        _ = self._tasks.spawn(
            self._integrate_bounded(plan),
            event=INITIATIVE_INTEGRATION_FAILED,
            plan_id=plan_id,
        )

    async def drain(self, *, timeout_sec: float) -> None:
        """Wait for outstanding integration dispatches at shutdown."""
        await self._tasks.drain(timeout_sec=timeout_sec)

    async def _integrate_bounded(self, plan: Plan) -> None:
        """Run one integration dispatch, swallowing every non-critical failure.

        A failure leaves the plan INTEGRATING with no integration task, which
        the next rollup event re-fires. The plan never advances on a failure:
        the tail is fail-closed.

        Raises:
            asyncio.CancelledError: If the task is cancelled at shutdown; it
                propagates so the background registry can reap it.
        """
        try:
            await asyncio.wait_for(
                self._run(plan), timeout=await self._timeout_seconds()
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                INITIATIVE_INTEGRATION_FAILED, plan_id=str(plan.id), reason="timeout"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort tail; the plan stays
            # INTEGRATING and the next rollup event re-fires the stage
            reraise_critical(exc)
            logger.warning(
                INITIATIVE_INTEGRATION_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        finally:
            self._inflight.discard(str(plan.id))

    async def _run(self, plan: Plan) -> None:
        """Mint the integration task if it is still needed, then dispatch it."""
        fresh = await self._persistence.plans.get(NotBlankStr(str(plan.id)))
        if fresh is None or fresh.status is not PlanStatus.INTEGRATING:
            logger.debug(
                INITIATIVE_INTEGRATION_SKIPPED,
                plan_id=str(plan.id),
                reason="no_longer_integrating",
                status=fresh.status.value if fresh else None,
            )
            return
        existing = await self._persistence.tasks.get(integration_task_id(fresh))
        if existing is not None:
            logger.debug(
                INITIATIVE_INTEGRATION_SKIPPED,
                plan_id=str(fresh.id),
                task_id=str(existing.id),
                reason="already_minted",
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
        logger.info(
            INITIATIVE_INTEGRATION_STARTED,
            plan_id=str(fresh.id),
            project=str(fresh.project),
        )
        await self._dispatch(fresh, objective)

    async def _dispatch(self, plan: Plan, objective: Task) -> None:
        """Persist the integration task and run it through the work spine."""
        brief = build_integration_brief(plan)
        task = Task(
            id=integration_task_uuid(plan),
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
