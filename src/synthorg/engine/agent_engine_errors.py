"""Error handling mixin for :class:`AgentEngine`.

Extracts completion logging, provider degradation, and fatal-error
handling into a mixin so the main module stays under the size limit.
"""

from typing import TYPE_CHECKING, Any

from synthorg.budget.errors import BudgetExhaustedError, QuotaExhaustedError
from synthorg.budget.quota import DegradationAction
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.metrics import TaskCompletionMetrics
from synthorg.engine.prompt import build_error_prompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.sanitization import sanitize_message
from synthorg.engine.task_sync import sync_to_task_engine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.degradation import DEGRADATION_PROVIDER_SWAPPED
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_BUDGET_STOPPED,
    EXECUTION_ENGINE_COMPLETE,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_TASK_METRICS,
    EXECUTION_ENGINE_TASK_TRANSITION,
)
from synthorg.observability.events.prompt import PROMPT_TOKEN_RATIO_HIGH
from synthorg.providers.errors import DriverNotRegisteredError

if TYPE_CHECKING:
    from synthorg.budget.degradation import PreFlightResult
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.prompt import SystemPrompt
    from synthorg.providers.models import CompletionConfig
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)

_PROMPT_TOKEN_RATIO_THRESHOLD: float = 0.3


class AgentEngineErrorsMixin:
    """Mixin providing completion logging + error/degradation handlers."""

    _provider_registry: ProviderRegistry | None
    _task_engine: Any
    _apply_recovery: Any

    def _log_completion(
        self,
        result: AgentRunResult,
        agent_id: str,
        task_id: str,
        duration: float,
    ) -> None:
        """Log structured completion event and proxy overhead metrics."""
        accumulated = result.execution_result.context.accumulated_cost
        logger.info(
            EXECUTION_ENGINE_COMPLETE,
            agent_id=agent_id,
            task_id=task_id,
            termination_reason=result.termination_reason.value,
            total_turns=result.total_turns,
            total_tokens=accumulated.total_tokens,
            duration_seconds=duration,
            cost=result.total_cost,
        )

        metrics = TaskCompletionMetrics.from_run_result(result)
        logger.info(
            EXECUTION_ENGINE_TASK_METRICS,
            agent_id=agent_id,
            task_id=task_id,
            termination_reason=result.termination_reason.value,
            turns_per_task=metrics.turns_per_task,
            tokens_per_task=metrics.tokens_per_task,
            cost_per_task=metrics.cost_per_task,
            duration_seconds=metrics.duration_seconds,
            prompt_tokens=metrics.prompt_tokens,
            prompt_token_ratio=metrics.prompt_token_ratio,
        )

        if metrics.prompt_token_ratio > _PROMPT_TOKEN_RATIO_THRESHOLD:
            logger.warning(
                PROMPT_TOKEN_RATIO_HIGH,
                agent_id=agent_id,
                task_id=task_id,
                prompt_token_ratio=metrics.prompt_token_ratio,
                prompt_tokens=metrics.prompt_tokens,
                total_tokens=metrics.tokens_per_task,
            )

    def _apply_degradation(
        self,
        preflight: PreFlightResult,
        identity: AgentIdentity,
        provider: CompletionProvider,
    ) -> tuple[CompletionProvider, AgentIdentity]:
        """Apply degradation result: swap provider if FALLBACK selected."""
        effective = preflight.effective_provider
        if effective is None or effective == identity.model.provider:
            return provider, identity

        original = identity.model.provider
        if self._provider_registry is None:
            logger.warning(
                DEGRADATION_PROVIDER_SWAPPED,
                original_provider=original,
                fallback_provider=effective,
                error="no provider_registry available",
                result="failed",
            )
            msg = (
                f"FALLBACK selected provider {effective!r} "
                f"but no provider_registry available"
            )
            raise QuotaExhaustedError(
                msg,
                provider_name=original,
                degradation_action=DegradationAction.FALLBACK,
            )

        try:
            new_provider = self._provider_registry.get(effective)
        except DriverNotRegisteredError as exc:
            logger.warning(
                DEGRADATION_PROVIDER_SWAPPED,
                original_provider=original,
                fallback_provider=effective,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                result="failed",
            )
            msg = f"Fallback provider {effective!r} not found in registry"
            raise QuotaExhaustedError(
                msg,
                provider_name=original,
                degradation_action=DegradationAction.FALLBACK,
            ) from exc

        logger.info(
            DEGRADATION_PROVIDER_SWAPPED,
            original_provider=identity.model.provider,
            fallback_provider=effective,
            result="success",
        )
        new_identity = identity.model_copy(
            update={
                "model": identity.model.model_copy(
                    update={"provider": effective},
                ),
            },
        )
        return new_provider, new_identity

    def _handle_budget_error(  # noqa: PLR0913
        self,
        *,
        exc: BudgetExhaustedError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = None,
        system_prompt: SystemPrompt | None = None,
    ) -> AgentRunResult:
        """Build a BUDGET_EXHAUSTED result (no recovery -- controlled stop)."""
        logger.warning(
            EXECUTION_ENGINE_BUDGET_STOPPED,
            agent_id=agent_id,
            task_id=task_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        try:
            error_ctx = ctx or AgentContext.from_identity(identity, task=task)
            budget_result = ExecutionResult(
                context=error_ctx,
                termination_reason=TerminationReason.BUDGET_EXHAUSTED,
            )
            error_prompt = build_error_prompt(
                identity,
                agent_id,
                system_prompt,
            )
            return AgentRunResult(
                execution_result=budget_result,
                system_prompt=error_prompt,
                duration_seconds=duration_seconds,
                agent_id=agent_id,
                task_id=task_id,
            )
        except MemoryError, RecursionError:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error while building budget-exhausted result",
            )
            raise
        except Exception as build_exc:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Failed to build budget-exhausted result: {build_exc}",
            )
            exc.add_note(
                f"Secondary failure while building budget-exhausted "
                f"result: {type(build_exc).__name__}: {build_exc}",
            )
            raise exc from None

    async def _handle_fatal_error(  # noqa: PLR0913
        self,
        *,
        exc: Exception,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = None,
        system_prompt: SystemPrompt | None = None,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
    ) -> AgentRunResult:
        """Build an error ``AgentRunResult`` when the execution pipeline fails."""
        raw_msg = str(exc)
        sanitized = sanitize_message(raw_msg)
        error_msg = f"{type(exc).__name__}: {sanitized}"
        logger.exception(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error=error_msg,
        )

        pre_fatal_status = (
            ctx.task_execution.status
            if ctx is not None and ctx.task_execution is not None
            else None
        )
        try:
            error_execution = await self._build_error_execution(
                identity,
                task,
                agent_id,
                task_id,
                error_msg,
                ctx,
                completion_config=completion_config,
                effective_autonomy=effective_autonomy,
                provider=provider,
            )
            error_ctx = error_execution.context
            if (
                error_ctx.task_execution is not None
                and pre_fatal_status is not None
                and error_ctx.task_execution.status != pre_fatal_status
            ):
                logger.info(
                    EXECUTION_ENGINE_TASK_TRANSITION,
                    agent_id=agent_id,
                    task_id=task_id,
                    from_status=pre_fatal_status.value,
                    to_status=error_ctx.task_execution.status.value,
                )
                await sync_to_task_engine(
                    self._task_engine,
                    target_status=error_ctx.task_execution.status,
                    task_id=task_id,
                    agent_id=agent_id,
                    reason=f"Fatal error recovery: {type(exc).__name__}",
                )
            error_prompt = build_error_prompt(
                identity,
                agent_id,
                system_prompt,
            )
            return AgentRunResult(
                execution_result=error_execution,
                system_prompt=error_prompt,
                duration_seconds=duration_seconds,
                agent_id=agent_id,
                task_id=task_id,
            )
        except MemoryError, RecursionError:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error while building error result",
            )
            raise
        except Exception as build_exc:
            logger.exception(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error=f"Failed to build error result: {build_exc}",
                original_error=error_msg,
            )
            exc.add_note(
                f"Secondary failure while building error result: "
                f"{type(build_exc).__name__}: {build_exc}",
            )
            raise exc from None

    async def _build_error_execution(  # noqa: PLR0913
        self,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        error_msg: str,
        ctx: AgentContext | None,
        *,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
    ) -> ExecutionResult:
        """Create an error ``ExecutionResult`` and apply recovery."""
        error_ctx = ctx or AgentContext.from_identity(identity, task=task)
        error_execution = ExecutionResult(
            context=error_ctx,
            termination_reason=TerminationReason.ERROR,
            error_message=error_msg,
        )
        result, _ = await self._apply_recovery(
            error_execution,
            identity,
            agent_id,
            task_id,
            completion_config=completion_config,
            effective_autonomy=effective_autonomy,
            provider=provider,
        )
        return result  # type: ignore[no-any-return]
