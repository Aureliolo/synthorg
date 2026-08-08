# module-kind: service
"""Error handling mixin for :class:`AgentEngine`.

Extracts completion logging, provider degradation, and fatal-error
handling into a mixin so the main module stays under the size limit.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.degradation import PreFlightResult
from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import DegradationAction
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine._task_sync_engine import sync_to_task_engine
from synthorg.engine.agent_engine_budget_halt import AgentEngineBudgetHaltMixin
from synthorg.engine.context import AgentContext
from synthorg.engine.cost_recording import resolve_tracker_currency
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.metrics import TaskCompletionMetrics
from synthorg.engine.prompt import SystemPrompt, build_error_prompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.sanitization import sanitize_message
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.degradation import DEGRADATION_PROVIDER_SWAPPED
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_COMPLETE,
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_TASK_METRICS,
    EXECUTION_ENGINE_TASK_TRANSITION,
)
from synthorg.observability.events.prompt import PROMPT_TOKEN_RATIO_HIGH
from synthorg.observability.events.stakes_routing import (
    STAKES_ROUTING_PROVIDER_SWITCHED,
    STAKES_ROUTING_PROVIDER_UNRESOLVED,
)
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine._agent_engine_callables import ApplyRecovery
    from synthorg.engine.task_engine import TaskEngine

logger = get_logger(__name__)


class FatalFailure(BaseModel):
    """What the fatal boundary knows about the exception it caught.

    The message and the exception's class travel together because the
    diagnosis reads both: the class is the classification (a provider
    that refused a request said so in its own type), the message is the
    fallback when there is no typed cause. Passing only the message is
    what left a precise ``ProviderError`` diagnosed ``unknown``.

    Attributes:
        message: Credential-scrubbed, path-sanitised description, safe to
            put back into agent context.
        error_type: ``type(exc).__name__``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    message: str = Field(description="Sanitised failure description")
    error_type: NotBlankStr = Field(description="Exception class name")


_PROMPT_TOKEN_RATIO_THRESHOLD: float = 0.3


class AgentEngineErrorsMixin(AgentEngineBudgetHaltMixin):
    """Mixin providing completion logging + error/degradation handlers."""

    _provider_registry: ProviderRegistry | None
    _task_engine: TaskEngine | None
    _apply_recovery: ApplyRecovery

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

    def _dispatch_client_for(
        self,
        identity: AgentIdentity,
        fallback_provider: CompletionProvider,
    ) -> CompletionProvider:
        """Return the client that serves ``identity.model.provider``.

        The engine holds a single default client, but each agent can be
        pinned to any registered provider. Cost attribution and the budget
        preflight both read ``identity.model.provider``, so the dispatched
        client must be that same provider or a call hits one provider's API
        while the cost/quota lands on another. Resolving strictly against the
        registry keeps the client and the identity in lockstep; a miss (an
        agent pinned to an unregistered provider) raises
        ``DriverNotRegisteredError`` so the run fails cleanly here instead of
        silently dispatching a mismatched pair to the engine default. Falls
        back to ``fallback_provider`` only when no registry is wired at all
        (a degraded / test context with no catalogue to resolve against).

        Returns:
            The registry client for ``identity.model.provider``, or
            ``fallback_provider`` when no provider registry is wired.

        Raises:
            DriverNotRegisteredError: When a registry is wired but does not
                know ``identity.model.provider``.
        """
        if self._provider_registry is None:
            return fallback_provider
        return self._provider_registry.get(identity.model.provider)

    def _resolve_provider_instance(
        self,
        routed: AgentIdentity,
        fallback_identity: AgentIdentity,
        fallback_provider: CompletionProvider,
    ) -> tuple[CompletionProvider, AgentIdentity]:
        """Return the client that serves the routed model's provider.

        Stakes routing can pick a model owned by a provider other than the
        engine default. Cost attribution reads ``identity.model.provider``,
        so the dispatched client must be that same provider or a call would
        hit one provider's API while the cost lands on another. Mirrors
        :meth:`_apply_degradation`'s registry lookup.

        When the routed provider matches the pre-routing one, the instance is
        unchanged (only the model id/tier moved). When it cannot be resolved
        (no registry wired, or a name the registry does not know), the
        pre-routing ``fallback_identity`` + ``fallback_provider`` are kept so
        instance and attribution stay in lockstep: a routing miss is never a
        mis-attribution.

        Returns:
            ``(provider, identity)``: the resolved client + routed identity,
            or the fallback pair when the routed provider is unresolvable.
        """
        target = routed.model.provider
        if target == fallback_identity.model.provider:
            # Same provider as before routing. ``fallback_provider`` was
            # resolved for that provider at run start (``_dispatch_client_for``),
            # so it already serves ``target``; only the model id/tier moved.
            return fallback_provider, routed
        if self._provider_registry is None:
            logger.warning(
                STAKES_ROUTING_PROVIDER_UNRESOLVED,
                routed_provider=target,
                reason="no_provider_registry",
                result="kept_default",
            )
            return fallback_provider, fallback_identity
        try:
            new_provider = self._provider_registry.get(target)
        except DriverNotRegisteredError as exc:
            logger.warning(
                STAKES_ROUTING_PROVIDER_UNRESOLVED,
                routed_provider=target,
                reason="not_in_registry",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                result="kept_default",
            )
            return fallback_provider, fallback_identity
        logger.info(
            STAKES_ROUTING_PROVIDER_SWITCHED,
            from_provider=fallback_identity.model.provider,
            to_provider=target,
            model_id=routed.model.model_id,
        )
        return new_provider, routed

    def _apply_degradation(
        self,
        preflight: PreFlightResult,
        identity: AgentIdentity,
        provider: CompletionProvider,
    ) -> tuple[CompletionProvider, AgentIdentity]:
        """Apply degradation result: swap provider if FALLBACK selected.

        Returns:
            ``(provider, identity)``: the swapped-in provider plus the
            identity copy carrying the fallback provider name, or the
            original pair when no swap was needed.

        Raises:
            QuotaExhaustedError: If FALLBACK selected a provider but
                no ``provider_registry`` is wired, or the registry
                does not know the fallback provider name.
        """
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
        """Build an error ``AgentRunResult`` when the execution pipeline fails.

        Returns:
            An :class:`AgentRunResult` whose
            ``execution_result.termination_reason`` is ``ERROR`` and
            whose ``error_message`` is the sanitised description of
            ``exc``; on a secondary failure during error-result
            construction the original ``exc`` is re-raised with a
            ``note`` describing the secondary error.
        """
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
                agent_id=agent_id,
                task_id=task_id,
                failure=FatalFailure(
                    message=error_msg,
                    error_type=NotBlankStr(type(exc).__name__),
                ),
                ctx=ctx,
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
        except Exception as build_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(build_exc)
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
        *,
        agent_id: str,
        task_id: str,
        failure: FatalFailure,
        ctx: AgentContext | None,
        completion_config: CompletionConfig | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        provider: CompletionProvider | None = None,
    ) -> ExecutionResult:
        """Create an error ``ExecutionResult`` and apply recovery.

        Returns:
            The :class:`ExecutionResult` after recovery has been
            applied (the engine's recovery hook may rewrite the
            termination reason or context).
        """
        error_ctx = ctx or AgentContext.from_identity(identity, task=task)
        error_execution = ExecutionResult(
            context=error_ctx,
            termination_reason=TerminationReason.ERROR,
            error_message=failure.message,
            error_type=failure.error_type,
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
        return result
