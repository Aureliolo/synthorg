# module-kind: service
"""Checkpoint-resume steps for :class:`AgentEngine`.

The recovery mixin decides *whether* to resume and drives the resume; the
individual steps live here: establish the budget the resumed run answers to
and read its checkpoint, run the loop on a reconstituted context, then record
costs, apply the post-execution transitions and clear the checkpoint
artefacts.

Split from the recovery mixin so the decision and the steps have separate
homes; :class:`AgentEngineRecoveryMixin` inherits it, so the engine still sees
one flat surface.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine._ceiling_publish import sync_ctx_ceilings
from synthorg.engine.artifacts.baseline_scope import RunBaselineProbe
from synthorg.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import record_execution_costs
from synthorg.engine.errors import RecoveryCheckpointMissingError
from synthorg.engine.loop_budget_signal import resolve_budget_signal_config
from synthorg.engine.loop_empty_run import resolve_produce_early_percent
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.mcp_tool_retrieval import task_brief_text
from synthorg.engine.recovery import RecoveryResult, RecoveryStrategy
from synthorg.engine.task_sync import apply_post_execution_transitions
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_RESUME_COMPLETE,
    EXECUTION_RESUME_FAILED,
    EXECUTION_RESUME_START,
)
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.core.clock import Clock
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine._agent_engine_callables import (
        BuildBudgetChecker,
        MakeLoopWithCallback,
        MakeToolInvoker,
        ResolveMemoryStrategy,
        ValidateProject,
    )
    from synthorg.engine.checkpoint.wiring import CheckpointWiring
    from synthorg.engine.flight_recording import FlightRecorderSink
    from synthorg.engine.loop_protocol import ExecutionLoop, ShutdownChecker
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class ResumePreparation(BaseModel):
    """What a resume needs settled before the loop is reconstructed.

    The project id travels with the budget because validating the project is
    what establishes both: a resume under a project runs against that
    project's remaining budget, and reading one without the other would let
    the loop spend against a budget it was not admitted to.

    Attributes:
        checkpoint_json: Serialised checkpoint context to reconstruct from.
        project_id: Project the resumed run belongs to, if any.
        project_budget: Remaining project budget the resumed loop runs under.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    checkpoint_json: str = Field(description="Serialised checkpoint context")
    project_id: str | None = Field(default=None, description="Owning project id")
    project_budget: float = Field(
        default=0.0,
        ge=0.0,
        description="Remaining project budget for the resumed run",
    )


class AgentEngineCheckpointResumeMixin:
    """Mixin performing a checkpoint resume once recovery has chosen one."""

    _recovery_strategy: RecoveryStrategy | None
    _project_repo: ProjectRepository | None
    _validate_project: ValidateProject
    _build_budget_checker: BuildBudgetChecker
    _budget_enforcer: BudgetEnforcer | None
    _config_resolver: ConfigResolver | None
    _loop: ExecutionLoop
    _make_loop_with_callback: MakeLoopWithCallback
    _provider: CompletionProvider
    _make_tool_invoker: MakeToolInvoker
    _resolve_memory_strategy: ResolveMemoryStrategy
    _shutdown_checker: ShutdownChecker | None
    _cost_tracker: CostTrackerProtocol | None
    _task_engine: TaskEngine | None
    _run_probe: RunBaselineProbe | None
    _approval_store: ApprovalStoreProtocol | None
    _checkpointing: CheckpointWiring | None
    _flight_recorder_sink: FlightRecorderSink | None
    _clock: Clock

    def _validate_checkpoint_json(
        self,
        recovery_result: RecoveryResult,
        agent_id: str,
        task_id: str,
    ) -> str:
        """Return checkpoint JSON or raise if unexpectedly absent.

        Returns:
            The serialised checkpoint context JSON string from
            ``recovery_result``.

        Raises:
            RecoveryCheckpointMissingError: If ``checkpoint_context_json``
                is ``None`` despite the strategy reporting ``can_resume``
                true.
        """
        if recovery_result.checkpoint_context_json is None:
            logger.error(
                EXECUTION_RESUME_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                error="checkpoint_context_json is None but can_resume was True",
            )
            msg = "checkpoint_context_json is None but can_resume was True"
            raise RecoveryCheckpointMissingError(msg)
        return recovery_result.checkpoint_context_json

    async def _prepare_resume(
        self,
        recovery_result: RecoveryResult,
        task: Task,
        agent_id: str,
        task_id: str,
        *,
        project_id: str | None,
    ) -> ResumePreparation:
        """Establish the project budget and checkpoint a resume needs.

        Returns:
            The validated checkpoint JSON plus the project id and budget the
            resumed loop runs under.
        """
        project_budget = 0.0
        if self._project_repo is not None:
            project_budget = await self._validate_project(
                task=task,
                agent_id=agent_id,
                task_id=task_id,
            )
            project_id = task.project
        checkpoint_json = self._validate_checkpoint_json(
            recovery_result,
            agent_id,
            task_id,
        )
        logger.info(
            EXECUTION_RESUME_START,
            agent_id=agent_id,
            task_id=task_id,
            resume_attempt=recovery_result.resume_attempt,
        )
        return ResumePreparation(
            checkpoint_json=checkpoint_json,
            project_id=project_id,
            project_budget=project_budget,
        )

    async def _execute_resumed_loop(
        self,
        checkpoint_ctx: AgentContext,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
        project_id: str | None = None,
        project_budget: float = 0.0,
    ) -> ExecutionResult:
        """Run the execution loop on a reconstituted checkpoint context.

        Returns:
            The :class:`ExecutionResult` from running the engine's
            configured loop against the reconciled checkpoint context.
        """
        budget_checker = (
            await self._build_budget_checker(
                checkpoint_ctx.task_execution.task,
                agent_id,
                project_id=project_id,
                project_budget=project_budget,
            )
            if checkpoint_ctx.task_execution is not None
            else None
        )
        # The checkpoint carries whatever ceilings were in force when it was
        # taken; the checker just built above is what actually enforces this
        # resumed run and may disagree, e.g. Task.hard_token_ceiling unset
        # and budget.run_hard_token_ceiling changed while the run was parked,
        # or disabled entirely. Without this, check_budget_signal and
        # nudge_unproductive_spend read a threshold the loop is not the one
        # enforcing.
        checkpoint_ctx = sync_ctx_ceilings(checkpoint_ctx, budget_checker)
        # A checkpoint-resumed run is exactly the long, budget-bounded
        # session these two exist to keep legible; without them a run
        # surviving a restart loses its turn-boundary remainder report and
        # its terminal warning for the rest of its life.
        budget_signal_config = await resolve_budget_signal_config(self._config_resolver)
        produce_early_percent = await resolve_produce_early_percent(
            self._config_resolver
        )

        loop = self._make_loop_with_callback(self._loop, agent_id, task_id)
        result: ExecutionResult = await loop.execute(
            context=checkpoint_ctx,
            provider=provider or self._provider,
            tool_invoker=self._make_tool_invoker(
                checkpoint_ctx.identity,
                task_id=task_id,
                effective_autonomy=effective_autonomy,
                project_id=project_id,
                memory_strategy=self._resolve_memory_strategy(),
                retrieval_query=(
                    task_brief_text(checkpoint_ctx.task_execution.task)
                    if checkpoint_ctx.task_execution is not None
                    else None
                ),
            ),
            budget_checker=budget_checker,
            shutdown_checker=self._shutdown_checker,
            completion_config=completion_config,
            budget_signal_config=budget_signal_config,
            produce_early_percent=produce_early_percent,
        )
        return result

    async def _finalize_resume(
        self,
        result: ExecutionResult,
        identity: AgentIdentity,
        execution_id: str,
        agent_id: str,
        task_id: str,
        *,
        project_id: str | None = None,
    ) -> ExecutionResult:
        """Record costs, apply transitions, and clean up after resume.

        Returns:
            The :class:`ExecutionResult` with post-execution task-state
            transitions applied; checkpoint artefacts are cleaned up
            when the resumed run did not terminate with ``ERROR``.

        Raises:
            ExecutionStateError: When a post-execution transition cannot
                land. The cleanup still runs first: a task whose state
                could not be moved must not also leave its checkpoint and
                heartbeat rows behind for a resume that will never come.
        """
        await record_execution_costs(
            result,
            identity,
            agent_id,
            task_id,
            tracker=self._cost_tracker,
            project_id=project_id,
        )
        # Local: the ``flight_recording`` hub pulls its replay service, which
        # reaches the red-team package and back into this engine, so importing
        # it at module scope closes a cold-import cycle.
        from synthorg.engine.flight_recording import (  # noqa: PLC0415
            record_run_frames,
        )

        # The recovered turns are part of this execution and share its id, so
        # without this the run's own history stops at the turn it died on and
        # a replay shows a failure that was recovered from.
        await record_run_frames(
            result,
            sink=self._flight_recorder_sink,
            agent_id=agent_id,
            task_id=task_id,
            clock=self._clock,
        )
        try:
            result = await apply_post_execution_transitions(
                result,
                agent_id=agent_id,
                task_id=task_id,
                task_engine=self._task_engine,
                approval_store=self._approval_store,
                run_probe=self._run_probe,
            )
            logger.info(
                EXECUTION_RESUME_COMPLETE,
                agent_id=agent_id,
                task_id=task_id,
                termination_reason=result.termination_reason.value,
            )
        finally:
            if result.termination_reason != TerminationReason.ERROR:
                if self._recovery_strategy is not None:
                    await self._recovery_strategy.finalize(execution_id)
                await cleanup_checkpoint_artifacts(self._checkpointing, execution_id)
        return result
