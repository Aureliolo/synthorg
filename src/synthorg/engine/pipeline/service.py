"""Default work pipeline implementation.

Composes the already-wired runtime services into the single spine:
intake -> projects -> decompose (solo-vs-team verdict) -> solo OR
team execution -> coordination metrics. The spine owns no
user-facing choice; the verdict is produced by the injected
:class:`WorkRoutingPolicy`. Coordination metrics are emitted by the
shared collector already threaded into the boot ``AgentEngine`` and
the coordinator; the spine records the stage but never re-collects.
"""

from typing import TYPE_CHECKING, TypeVar

from synthorg.client.models import ClientRequest, TaskRequirement
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task  # noqa: TC001
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.pipeline.errors import (
    WorkIntakeRejectedError,
    WorkPipelineError,
    WorkPipelineTeamPathUnavailableError,
    WorkProjectNotFoundError,
    WorkRoutingUndecidableError,
)
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
)
from synthorg.engine.stakes import build_stakes_assessor
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.pipeline import (
    PIPELINE_PHASE_COMPLETED,
    PIPELINE_PHASE_FAILED,
    PIPELINE_RUN_COMPLETED,
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_STARTED,
    PIPELINE_SOLO_AGENT_SELECTED,
)
from synthorg.observability.events.stakes_routing import STAKES_ASSESSED

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.intake.engine import IntakeEngine
    from synthorg.engine.pipeline.policy.protocol import WorkRoutingPolicy
    from synthorg.engine.routing.scorer import AgentTaskScorer
    from synthorg.engine.stakes.protocol import StakesAssessor
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.workers.execution_service import WorkerExecutionService

logger = get_logger(__name__)

_T = TypeVar("_T")

_PHASE_INTAKE = "intake"
_PHASE_PROJECTS = "projects"
_PHASE_DECOMPOSE = "decompose"
_PHASE_SOLO = "solo_execution"
_PHASE_TEAM = "team_execution"
_PHASE_METRICS = "coordination_metrics"


class DefaultWorkPipeline:
    """The shipped :class:`WorkPipeline` implementation.

    Args:
        intake_engine: Walks a request through intake.
        task_engine: Single-writer task state owner.
        project_repository: Read seam for project-context binding.
        routing_policy: Owns the solo-vs-team verdict.
        scorer: Shared agent-task scorer (also used by the
            coordinator) for the solo-path single-agent pick.
        worker_execution_service: Solo-path executor.
        coordinator: Team-path coordinator; ``None`` in an
            empty-company boot.
        agent_registry: Active-agent pool source.
        clock: Injectable time source (defaults to the system clock).
    """

    __slots__ = (
        "_agent_registry",
        "_clock",
        "_coordinator",
        "_intake_engine",
        "_project_repository",
        "_routing_policy",
        "_scorer",
        "_stakes_assessor",
        "_task_engine",
        "_worker_execution_service",
    )

    def __init__(  # noqa: PLR0913 -- keyword-only dependency injection
        self,
        *,
        intake_engine: IntakeEngine,
        task_engine: TaskEngine,
        project_repository: ProjectRepository,
        routing_policy: WorkRoutingPolicy,
        scorer: AgentTaskScorer,
        worker_execution_service: WorkerExecutionService,
        coordinator: MultiAgentCoordinator | None,
        agent_registry: AgentRegistryService,
        clock: Clock | None = None,
        stakes_assessor: StakesAssessor | None = None,
    ) -> None:
        self._intake_engine = intake_engine
        self._task_engine = task_engine
        self._project_repository = project_repository
        self._routing_policy = routing_policy
        self._scorer = scorer
        self._worker_execution_service = worker_execution_service
        self._coordinator = coordinator
        self._agent_registry = agent_registry
        self._clock = clock if clock is not None else SystemClock()
        self._stakes_assessor = stakes_assessor or build_stakes_assessor()

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        """Drive *work_item* through the full spine (see module docstring)."""
        started = self._clock.monotonic()
        phases: list[WorkPhaseResult] = []
        logger.info(
            PIPELINE_RUN_STARTED,
            correlation_id=work_item.correlation_id,
            source=work_item.source.value,
            project=work_item.project,
        )
        try:
            task = await self._phase(phases, _PHASE_INTAKE, self._intake(work_item))
            await self._phase(phases, _PHASE_PROJECTS, self._resolve_project(work_item))
            verdict, agents = await self._phase(
                phases, _PHASE_DECOMPOSE, self._decompose(task)
            )
            if verdict is RoutingVerdict.LEAF:
                path = ExecutionPath.SOLO
                final_status = await self._phase(
                    phases, _PHASE_SOLO, self._run_solo(work_item, task, agents)
                )
            else:
                path = ExecutionPath.TEAM
                final_status = await self._phase(
                    phases, _PHASE_TEAM, self._run_team(work_item, task, agents)
                )
            await self._phase(phases, _PHASE_METRICS, self._metrics_stage())
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                PIPELINE_RUN_FAILED,
                correlation_id=work_item.correlation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        total = max(0.0, self._clock.monotonic() - started)
        result = WorkPipelineResult(
            work_item=work_item,
            verdict=verdict,
            execution_path=path,
            task_id=task.id,
            final_task_status=final_status,
            phases=tuple(phases),
            total_duration_seconds=total,
        )
        logger.info(
            PIPELINE_RUN_COMPLETED,
            correlation_id=work_item.correlation_id,
            task_id=task.id,
            verdict=verdict.value,
            execution_path=path.value,
            final_task_status=final_status.value,
            total_duration_seconds=total,
        )
        return result

    async def _phase(
        self,
        phases: list[WorkPhaseResult],
        name: str,
        awaitable: Awaitable[_T],
    ) -> _T:
        """Await *awaitable*, recording a timed :class:`WorkPhaseResult`."""
        start = self._clock.monotonic()
        try:
            value = await awaitable
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = max(0.0, self._clock.monotonic() - start)
            phases.append(
                WorkPhaseResult(
                    phase=name,
                    success=False,
                    duration_seconds=elapsed,
                    error=safe_error_description(exc),
                )
            )
            logger.warning(
                PIPELINE_PHASE_FAILED,
                phase=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        elapsed = max(0.0, self._clock.monotonic() - start)
        phases.append(
            WorkPhaseResult(phase=name, success=True, duration_seconds=elapsed)
        )
        logger.info(PIPELINE_PHASE_COMPLETED, phase=name, duration_seconds=elapsed)
        return value

    async def _intake(self, work_item: WorkItem) -> Task:
        """Map the work item through intake and return the created task."""
        request = ClientRequest(
            request_id=work_item.correlation_id,
            client_id=work_item.origin_adapter_id,
            requirement=TaskRequirement(
                title=work_item.title,
                description=work_item.raw_intent,
                task_type=work_item.task_type,
                priority=work_item.priority,
                estimated_complexity=work_item.estimated_complexity,
                acceptance_criteria=work_item.acceptance_criteria,
            ),
            metadata={
                "source": work_item.source.value,
                "project": work_item.project,
                "requested_by": work_item.requested_by,
            },
        )
        _, result = await self._intake_engine.process(request)
        if not result.accepted:
            reason = result.rejection_reason or "intake rejected the request"
            raise WorkIntakeRejectedError(reason)
        if result.task_id is None:
            msg = "intake accepted the request but produced no task id"
            raise WorkIntakeRejectedError(msg)
        task = await self._task_engine.get_task(result.task_id)
        if task is None:
            msg = f"intake reported task {result.task_id!r} but it is not persisted"
            raise WorkIntakeRejectedError(msg)
        task = await self._link_forecast(task, work_item)
        return await self._assess_stakes(task, work_item)

    async def _assess_stakes(self, task: Task, work_item: WorkItem) -> Task:
        """Assess and stamp parent-task stakes for the LEAF (solo) path.

        The decomposition service assesses each subtask on the team path,
        but a LEAF task is executed directly without decomposition, so the
        parent task itself must carry its stakes for the routing layer.
        Stamped here, at the single intake funnel, so both paths converge.
        """
        stakes = self._stakes_assessor.assess_task(task)
        if stakes is task.stakes:
            return task
        updated = await self._task_engine.update_task(
            task.id,
            {"stakes": stakes},
            requested_by=work_item.requested_by,
        )
        logger.info(
            STAKES_ASSESSED,
            task_id=task.id,
            from_stakes=task.stakes.value,
            to_stakes=stakes.value,
            path="leaf",
        )
        return updated

    async def _link_forecast(self, task: Task, work_item: WorkItem) -> Task:
        """Stamp the approved forecast id + ceiling onto the task.

        The forecast gate releases an approved work item carrying its
        ``forecast_id`` and the operator-approved ``hard_ceiling``. The
        intake engine creates the task without those, so persist them
        here: the in-loop ``BudgetChecker`` reads ``Task.hard_ceiling``
        to enforce the per-brief ceiling, and the engine reads
        ``Task.forecast_id`` to stamp halt context for the resume banner.
        """
        updates: dict[str, object] = {}
        if work_item.forecast_id is not None:
            updates["forecast_id"] = work_item.forecast_id
        if work_item.hard_ceiling is not None:
            updates["hard_ceiling"] = work_item.hard_ceiling
        if not updates:
            return task
        return await self._task_engine.update_task(
            task.id,
            updates,
            requested_by=work_item.requested_by,
        )

    async def _resolve_project(self, work_item: WorkItem) -> None:
        """Bind the work to its project context (existence check)."""
        project = await self._project_repository.get(work_item.project)
        if project is None:
            msg = f"project {work_item.project!r} not found"
            raise WorkProjectNotFoundError(msg)

    async def _decompose(
        self,
        task: Task,
    ) -> tuple[RoutingVerdict, tuple[AgentIdentity, ...]]:
        """Fetch the active-agent pool and decide solo-vs-team.

        Both the agent-registry lookup and the routing decision run
        inside the decompose phase so registry errors and lookup
        latency are captured by the phase telemetry.
        """
        agents = await self._agent_registry.list_active()
        verdict = await self._routing_policy.decide(task=task, available_agents=agents)
        return verdict, agents

    async def _run_solo(
        self,
        work_item: WorkItem,
        task: Task,
        agents: tuple[AgentIdentity, ...],
    ) -> TaskStatus:
        """Assign one agent and execute the leaf task single-agent."""
        if not task.assigned_to:
            assigned_id = self._select_solo_agent(task, agents)
            await self._task_engine.transition_task(
                task.id,
                TaskStatus.ASSIGNED,
                requested_by=work_item.requested_by,
                reason="work pipeline solo routing",
                assigned_to=assigned_id,
            )
        post = await self._worker_execution_service.execute_once(
            task_id=task.id,
            previous_status=TaskStatus.ASSIGNED.value,
            new_status=TaskStatus.IN_PROGRESS.value,
            idempotency_key=work_item.correlation_id,
            requested_by=work_item.requested_by,
        )
        return post.status

    def _select_solo_agent(
        self,
        task: Task,
        agents: tuple[AgentIdentity, ...],
    ) -> str:
        """Pick the top-scoring viable agent for the leaf task."""
        if not agents:
            msg = "no active agents available for solo execution"
            raise WorkRoutingUndecidableError(msg)
        proxy = SubtaskDefinition(
            id=task.id,
            title=task.title,
            description=task.description,
            estimated_complexity=task.estimated_complexity,
        )
        candidates = [self._scorer.score(agent, proxy) for agent in agents]
        viable = [c for c in candidates if c.score >= self._scorer.min_score]
        if not viable:
            msg = (
                "no agent scored above the routing threshold "
                f"({self._scorer.min_score}) for solo execution"
            )
            raise WorkRoutingUndecidableError(msg)
        best = max(
            viable,
            key=lambda c: (c.score, str(c.agent_identity.id)),
        )
        assigned_id = str(best.agent_identity.id)
        logger.info(
            PIPELINE_SOLO_AGENT_SELECTED,
            task_id=task.id,
            agent_id=assigned_id,
            score=best.score,
        )
        return assigned_id

    async def _run_team(
        self,
        work_item: WorkItem,
        task: Task,
        agents: tuple[AgentIdentity, ...],
    ) -> TaskStatus:
        """Hand splittable work to the multi-agent coordinator."""
        del work_item
        if self._coordinator is None:
            raise WorkPipelineTeamPathUnavailableError
        if not agents:
            msg = "no active agents available for team coordination"
            raise WorkRoutingUndecidableError(msg)
        await self._coordinator.coordinate(
            CoordinationContext(task=task, available_agents=agents)
        )
        post = await self._task_engine.get_task(task.id)
        if post is None:
            msg = f"task {task.id!r} missing after coordination"
            raise WorkPipelineError(msg)
        return post.status

    async def _metrics_stage(self) -> None:
        """Record the coordination-metrics stage.

        Metrics records are written by the shared
        ``CoordinationMetricsCollector`` already threaded into the boot
        ``AgentEngine`` (solo) and the coordinator (team); the spine
        owns no separate collection to avoid a second, divergent
        source of truth.
        """
        return
