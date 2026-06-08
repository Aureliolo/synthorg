"""Builtin middleware wrappers for existing engine hooks.

Named slots in the middleware chain for configuration and ordering.
The actual hook logic remains inline in the execution pipeline
(``AgentEngine.run()``, ``ToolInvoker``, ``_post_execution_pipeline``).
These wrappers will delegate to the real implementations once the
agent middleware chain is wired into the execution loop.
"""

from typing import TYPE_CHECKING, override

from synthorg.budget.coordination_config import ErrorTaxonomyConfig
from synthorg.engine.middleware.models import (
    AgentMiddlewareContext,
    ModelCallResult,
    ToolCallResult,
)
from synthorg.engine.middleware.protocol import (
    BaseAgentMiddleware,
    ModelCallable,
    ToolCallable,
)
from synthorg.observability import get_logger
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)

if TYPE_CHECKING:
    # CostTracker / ApprovalGate are concrete services faked in tests; a runtime
    # import would make typeguard enforce a nominal isinstance the fakes fail.
    # security.protocol would cycle (security imports engine.prompt_safety).
    from synthorg.budget.tracker import CostTracker
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.security.protocol import SecurityInterceptionStrategy

logger = get_logger(__name__)


# ── SecurityInterceptorMiddleware ─────────────────────────────────


class SecurityInterceptorMiddleware(BaseAgentMiddleware):
    """Named slot for security interception in ``wrap_tool_call``.

    The actual interception is wired into ``ToolInvoker.invoke()``
    at construction.  This middleware reserves the ordering position
    so configuration can control where security runs relative to
    other middleware.

    Args:
        interceptor: The security interception strategy (stored for
            future direct delegation).
    """

    def __init__(
        self,
        *,
        interceptor: SecurityInterceptionStrategy | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="security_interceptor")
        self._interceptor = interceptor

    @override
    async def wrap_tool_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        """Delegate to inner call (interception wired in ToolInvoker).

        Returns:
            The inner :class:`ToolCallResult` unchanged; the real
            security check runs inside ``ToolInvoker.invoke()``.
        """
        # The security interceptor is wired into the ToolInvoker at
        # construction, not at the middleware level. This middleware
        # exists as a named slot in the chain for configuration and
        # ordering purposes. The actual interception happens inside
        # ToolInvoker.invoke().
        return await call(ctx)


# ── SanitizeMessageMiddleware ─────────────────────────────────────


class SanitizeMessageMiddleware(BaseAgentMiddleware):
    """Named slot for message sanitization in ``before_model``.

    The actual sanitization is applied inline in the execution
    pipeline (failure criteria, error messages).  This middleware
    reserves the ordering position.
    """

    def __init__(self, **_kwargs: object) -> None:
        super().__init__(name="sanitize_message")

    @override
    async def before_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Sanitize messages in context before model call.

        The actual sanitization is applied inline in the execution
        pipeline (failure criteria, error messages). This middleware
        provides the named slot for chain ordering.

        Returns:
            ``ctx`` unchanged; sanitisation is performed inline at
            the call sites that originate untrusted text.
        """
        return ctx


# ── ApprovalGateMiddleware ────────────────────────────────────────


class ApprovalGateMiddleware(BaseAgentMiddleware):
    """Named slot for approval gating in ``after_model``.

    The approval gate is wired into the execution loop at
    construction.  This middleware reserves the ordering position.

    Args:
        approval_gate: The approval gate instance (stored for
            future direct delegation).
    """

    def __init__(
        self,
        *,
        approval_gate: ApprovalGate | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="approval_gate")
        self._gate = approval_gate

    @override
    async def after_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Check for escalations after model response.

        The approval gate is wired into the execution loop at
        construction. This middleware provides the named slot.

        Returns:
            ``ctx`` unchanged; the approval gate runs in the
            execution loop, not in this middleware slot.
        """
        return ctx


# ── ClassificationMiddleware ──────────────────────────────────────


class ClassificationMiddleware(BaseAgentMiddleware):
    """Named slot for error classification in wrap hooks.

    Classification is invoked in ``_post_execution_pipeline``,
    not per-call.  This middleware reserves ordering positions
    in both ``wrap_model_call`` and ``wrap_tool_call``.

    Args:
        error_taxonomy_config: Configuration for error classification
            (stored for future direct delegation).
    """

    def __init__(
        self,
        *,
        error_taxonomy_config: ErrorTaxonomyConfig | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="classification")
        self._config = error_taxonomy_config

    @override
    async def wrap_model_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ModelCallable,
    ) -> ModelCallResult:
        """Delegate to inner call; classification runs post-execution.

        Returns:
            The inner :class:`ModelCallResult` unchanged.
        """
        # Classification is invoked in _post_execution_pipeline,
        # not per-call. This middleware provides the named slot.
        return await call(ctx)

    @override
    async def wrap_tool_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        """Delegate to inner call; classification runs post-execution.

        Returns:
            The inner :class:`ToolCallResult` unchanged.
        """
        return await call(ctx)


# ── CostRecordingMiddleware ───────────────────────────────────────


class CostRecordingMiddleware(BaseAgentMiddleware):
    """Named slot for cost recording in ``after_agent``.

    Cost recording is invoked in ``_post_execution_pipeline``.
    This middleware reserves the ordering position.

    Args:
        tracker: The cost tracker instance (stored for future
            direct delegation).
    """

    def __init__(
        self,
        *,
        tracker: CostTracker | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="cost_recording")
        self._tracker = tracker

    @override
    async def after_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Record execution costs (best-effort).

        Cost recording is invoked in _post_execution_pipeline.
        This middleware provides the named slot.

        Returns:
            ``ctx`` unchanged; cost recording runs in the
            post-execution pipeline, not in this slot.
        """
        return ctx


# ── CheckpointResumeMiddleware ────────────────────────────────────


class CheckpointResumeMiddleware(BaseAgentMiddleware):
    """Named slot for checkpoint resume in ``before_agent``.

    Checkpoint resume runs in ``AgentEngine._resume_from_checkpoint``.
    This middleware reserves the ordering position.

    Args:
        checkpoint_repo: Checkpoint persistence repository (stored
            for future direct delegation).
        heartbeat_repo: Heartbeat persistence repository (stored
            for future direct delegation).
    """

    def __init__(
        self,
        *,
        checkpoint_repo: CheckpointRepository | None = None,
        heartbeat_repo: HeartbeatRepository | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="checkpoint_resume")
        self._checkpoint_repo = checkpoint_repo
        self._heartbeat_repo = heartbeat_repo

    @override
    async def before_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Checkpoint resume runs in AgentEngine._resume_from_checkpoint.

        This middleware provides the named slot for chain ordering.

        Returns:
            ``ctx`` unchanged; checkpoint resume runs in the engine's
            dedicated path, not in this slot.
        """
        return ctx
