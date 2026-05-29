"""Policy gate middleware -- runtime pre-execution policy check.

Evaluates tool invocations against the configured ``PolicyEngine``
before execution.  In ``enforce`` mode, denied actions are blocked;
in ``log_only`` mode, denials are logged but the action proceeds.
"""

from typing import TYPE_CHECKING, override

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.middleware.protocol import BaseAgentMiddleware, ToolCallable
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_POLICY_DECISION_DENY,
    SECURITY_POLICY_LOG_ONLY_DENY,
)

if TYPE_CHECKING:
    from synthorg.engine.middleware.models import (
        AgentMiddlewareContext,
        ToolCallResult,
    )
    from synthorg.security.policy_engine.protocol import PolicyEngine

logger = get_logger(__name__)


class PolicyGateMiddleware(BaseAgentMiddleware):
    """Runtime policy gate in the ``wrap_tool_call`` slot.

    Evaluates each tool invocation against the configured policy
    engine.  When no engine is configured (``None``), the middleware
    is a transparent pass-through.

    Args:
        policy_engine: Policy engine instance, or ``None`` to disable.
        evaluation_mode: ``"enforce"`` or ``"log_only"``.
    """

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        evaluation_mode: str = "log_only",
        **_kwargs: object,
    ) -> None:
        super().__init__(name="policy_gate")
        if evaluation_mode not in ("enforce", "log_only"):
            msg = (
                f"evaluation_mode must be 'enforce' or 'log_only', "
                f"got {evaluation_mode!r}"
            )
            raise ValueError(msg)
        self._engine = policy_engine
        self._evaluation_mode = evaluation_mode

    @override
    async def wrap_tool_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        """Evaluate policy before tool execution.

        Args:
            ctx: Middleware context with agent/task metadata.
            call: Inner tool call to execute if policy allows.

        Returns:
            Tool call result (blocked if policy denies in enforce mode).
        """
        if self._engine is None:
            return await call(ctx)

        from synthorg.engine.middleware.models import (  # noqa: PLC0415
            ToolCallResult,
        )
        from synthorg.security.policy_engine.models import (  # noqa: PLC0415
            PolicyActionRequest,
        )

        try:
            # Build request inside the guarded block so metadata/
            # validation errors also follow the fallback path.
            tool_name = str(ctx.metadata.get("tool_name", "unknown"))
            request = PolicyActionRequest(
                action_type="tool_invoke",
                principal=str(getattr(ctx, "agent_id", "unknown")),
                resource=tool_name,
                context={
                    "task_id": str(getattr(ctx, "task_id", "unknown")),
                    "execution_id": str(
                        getattr(ctx, "execution_id", "unknown"),
                    ),
                },
            )
            decision = await self._engine.evaluate(request)
        except Exception as exc:
            reraise_critical(exc)
            from synthorg.observability.events.security import (  # noqa: PLC0415
                SECURITY_POLICY_ENGINE_ERROR,
            )

            logger.error(
                SECURITY_POLICY_ENGINE_ERROR,
            )
            # Fail-open: proceed to tool call on evaluation error.
            # CedarPolicyEngine handles fail_closed internally; this
            # catches errors outside the engine (e.g. request
            # construction, serialization).
            return await call(ctx)

        if not decision.allow:
            if self._evaluation_mode == "enforce":
                logger.warning(
                    SECURITY_POLICY_DECISION_DENY,
                    action_type=request.action_type,
                    principal=request.principal,
                    resource=request.resource,
                    reason=decision.reason,
                    latency_ms=decision.latency_ms,
                    mode="enforce",
                )
                return ToolCallResult(
                    tool_name=tool_name,
                    output="",
                    success=False,
                    error=f"Policy denied: {decision.reason}",
                )
            # log_only mode: log and proceed.
            logger.warning(
                SECURITY_POLICY_LOG_ONLY_DENY,
                action_type=request.action_type,
                principal=request.principal,
                resource=request.resource,
                reason=decision.reason,
                latency_ms=decision.latency_ms,
            )

        return await call(ctx)
