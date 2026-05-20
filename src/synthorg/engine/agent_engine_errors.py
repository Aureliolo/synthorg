"""Error handling mixin for :class:`AgentEngine`.

Extracts completion logging, provider degradation, and fatal-error
handling into a mixin so the main module stays under the size limit.
"""

from typing import TYPE_CHECKING, Any

from synthorg.budget.errors import (
    BudgetExhaustedError,
    QuotaExhaustedError,
    RunHardCeilingExceededError,
)
from synthorg.budget.quota import DegradationAction
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import resolve_tracker_currency
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

    async def _handle_budget_error(  # noqa: PLR0913
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
        """Build a BUDGET_EXHAUSTED (or PARKED, for hard ceiling) result.

        Hard-ceiling crossings route to a parked termination when the
        approval gate is wired so the operator can raise the ceiling
        and resume; the engine awaits ``ApprovalGate.park_context()``
        to persist the parked state. A persistence failure degrades
        gracefully to BUDGET_EXHAUSTED (the existing controlled-stop
        path) so a missing parked_context_repo never escalates to
        engine crash. All other ``BudgetExhaustedError`` subclasses
        (monthly / daily / project / quota) keep the original
        controlled-stop path.
        """
        logger.warning(
            EXECUTION_ENGINE_BUDGET_STOPPED,
            agent_id=agent_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        is_ceiling = isinstance(exc, RunHardCeilingExceededError)
        has_gate = getattr(self, "_approval_gate", None) is not None
        parked_ok = False
        if isinstance(exc, RunHardCeilingExceededError) and has_gate:
            parked_ok = await self._park_hard_ceiling(
                exc=exc,
                identity=identity,
                task=task,
                agent_id=agent_id,
                task_id=task_id,
                ctx=ctx,
            )
        try:
            error_ctx = ctx or AgentContext.from_identity(identity, task=task)
            termination = (
                TerminationReason.PARKED
                if is_ceiling and has_gate and parked_ok
                else TerminationReason.BUDGET_EXHAUSTED
            )
            budget_result = ExecutionResult(
                context=error_ctx,
                termination_reason=termination,
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
                currency=resolve_tracker_currency(
                    getattr(self, "_cost_tracker", None),
                ),
            )
        except MemoryError, RecursionError:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error while building budget-exhausted result",
            )
            raise
        except Exception as build_exc:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(build_exc).__name__,
                error=safe_error_description(build_exc),
                stage="build_budget_exhausted_result",
            )
            exc.add_note(
                f"Secondary failure while building budget-exhausted "
                f"result: {type(build_exc).__name__}: "
                f"{safe_error_description(build_exc)}",
            )
            raise exc from None

    async def _park_hard_ceiling(  # noqa: PLR0913
        self,
        *,
        exc: RunHardCeilingExceededError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        ctx: AgentContext | None,
    ) -> bool:
        """Persist a parked context for a hard-ceiling crossing.

        Returns ``True`` when ``ApprovalGate.park_context()`` succeeds.
        On failure (no parked-context repo, serialization error,
        persistence error) returns ``False`` so the caller degrades
        to the BUDGET_EXHAUSTED controlled-stop path. The failure is
        logged but never re-raised: a ceiling halt must not crash the
        engine even if the persistence layer is in a bad state.
        """
        from synthorg.approval.models import (  # noqa: PLC0415
            EscalationInfo,
        )
        from synthorg.core.enums import ApprovalRiskLevel  # noqa: PLC0415

        gate: Any = getattr(self, "_approval_gate", None)
        if gate is None:
            return False
        try:
            forecast_id_str = (
                str(exc.forecast_id) if exc.forecast_id is not None else "no-forecast"
            )
            reason = (
                f"Run hard ceiling exceeded: accumulated"
                f" {exc.accumulated_cost:.4f} {exc.currency}"
                f" >= ceiling {exc.ceiling_amount:.4f} {exc.currency}"
            )
            escalation = EscalationInfo(
                approval_id=f"hard-ceiling-{task_id}-{forecast_id_str}",
                tool_call_id=f"budget-checker-{task_id}",
                tool_name="budget_checker",
                action_type="budget:hard_ceiling_exceeded",
                risk_level=ApprovalRiskLevel.HIGH,
                reason=reason,
            )
            park_ctx = ctx or AgentContext.from_identity(identity, task=task)
            await gate.park_context(
                escalation=escalation,
                context=park_ctx,
                agent_id=agent_id,
                task_id=task_id,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as park_exc:
            logger.warning(
                EXECUTION_ENGINE_BUDGET_STOPPED,
                agent_id=agent_id,
                task_id=task_id,
                note="park_context failed; falling back to BUDGET_EXHAUSTED",
                error_type=type(park_exc).__name__,
                error=safe_error_description(park_exc),
            )
            return False
        return True

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
        # ``error_msg`` propagates back into agent context (the LLM
        # sees it on retry / handoff) and must not carry credential
        # material. Build it from ``safe_error_description`` (the
        # canonical credential scrubber); then run the result through
        # ``sanitize_message`` to additionally strip paths / URLs that
        # would otherwise leak operator-internal identifiers into the
        # LLM-context payload.
        error_desc = safe_error_description(exc)
        error_msg = sanitize_message(error_desc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=error_desc,
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
                currency=resolve_tracker_currency(
                    getattr(self, "_cost_tracker", None),
                ),
            )
        except MemoryError, RecursionError:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error="non-recoverable error while building error result",
            )
            raise
        except Exception as build_exc:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                error_type=type(build_exc).__name__,
                error=safe_error_description(build_exc),
                stage="build_error_result",
                original_error_type=type(exc).__name__,
            )
            exc.add_note(
                f"Secondary failure while building error result: "
                f"{type(build_exc).__name__}: "
                f"{safe_error_description(build_exc)}",
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
