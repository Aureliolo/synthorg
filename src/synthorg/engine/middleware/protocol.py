"""Agent-level middleware protocol and composition chain.

Defines the six-hook ``AgentMiddleware`` protocol, a base class
with no-op defaults, and the ``AgentMiddlewareChain`` compositor
that runs hooks in declared order (onion-style for wrap_* hooks,
linear for before_*/after_* hooks).
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.engine.middleware.models import (
    AgentMiddlewareContext,
    ModelCallResult,
    ToolCallResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.middleware import (
    MIDDLEWARE_AFTER_AGENT,
    MIDDLEWARE_AFTER_MODEL,
    MIDDLEWARE_BEFORE_AGENT,
    MIDDLEWARE_BEFORE_MODEL,
    MIDDLEWARE_HOOK_ERROR,
    MIDDLEWARE_WRAP_MODEL_CALL,
    MIDDLEWARE_WRAP_TOOL_CALL,
)

logger = get_logger(__name__)

# ── Callable type aliases ─────────────────────────────────────────

type ModelCallable = Callable[
    [AgentMiddlewareContext],
    Awaitable[ModelCallResult],
]
"""Async callable that performs the actual model (LLM) call."""

type ToolCallable = Callable[
    [AgentMiddlewareContext],
    Awaitable[ToolCallResult],
]
"""Async callable that performs the actual tool invocation."""


# ── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class AgentMiddleware(Protocol):
    """Agent-level middleware with six async hooks.

    Middleware is composed in declared order.  ``before_*`` hooks run
    left-to-right; ``after_*`` hooks run right-to-left for symmetric
    cleanup.  ``wrap_*`` hooks use onion composition (each wraps the
    next, innermost is the actual call).

    All hooks are async-only, consistent with the project's
    ``asyncio.TaskGroup`` patterns.
    """

    @property
    def name(self) -> NotBlankStr:
        """Unique middleware name for registry and logging."""
        ...

    async def before_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Once on invocation; load memory, validate input, record hashes."""
        ...

    async def before_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Before each model call; trim history, redact PII, inject context."""
        ...

    async def wrap_model_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ModelCallable,
    ) -> ModelCallResult:
        """Around model call; caching, dynamic tools, model swap."""
        ...

    async def wrap_tool_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        """Around tool execution; inject context, gate tools."""
        ...

    async def after_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """After model responds, before tools execute; human-in-loop checks."""
        ...

    async def after_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Once on completion; save results, notify, cleanup."""
        ...


# ── Base class with no-op defaults ────────────────────────────────


class BaseAgentMiddleware:
    """Base class providing no-op defaults for all six hooks.

    Subclass and override only the hooks you need.  The base
    ``wrap_model_call`` and ``wrap_tool_call`` simply delegate to
    the inner callable unchanged.

    Args:
        name: Unique middleware name for registry and logging.
    """

    __slots__ = ("_name",)

    def __init__(self, *, name: str) -> None:
        if not name or not name.strip():
            msg = "middleware name cannot be blank"
            logger.warning(
                MIDDLEWARE_HOOK_ERROR,
                error=msg,
                name=repr(name),
            )
            raise ValueError(msg)
        self._name: NotBlankStr = name.strip()

    @property
    def name(self) -> NotBlankStr:
        """Unique middleware name."""
        return self._name

    async def before_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """No-op: return context unchanged.

        Returns:
            The same ``ctx`` instance passed in.
        """
        return ctx

    async def before_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """No-op: return context unchanged.

        Returns:
            The same ``ctx`` instance passed in.
        """
        return ctx

    async def wrap_model_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ModelCallable,
    ) -> ModelCallResult:
        """No-op: delegate to inner callable unchanged.

        Returns:
            The result produced by ``call(ctx)``.
        """
        return await call(ctx)

    async def wrap_tool_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        """No-op: delegate to inner callable unchanged.

        Returns:
            The result produced by ``call(ctx)``.
        """
        return await call(ctx)

    async def after_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """No-op: return context unchanged.

        Returns:
            The same ``ctx`` instance passed in.
        """
        return ctx

    async def after_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """No-op: return context unchanged.

        Returns:
            The same ``ctx`` instance passed in.
        """
        return ctx


# ── Composition chain ─────────────────────────────────────────────


class AgentMiddlewareChain:
    """Composes an ordered tuple of ``AgentMiddleware`` instances.

    Composition rules:

    * **before_* hooks**: run left-to-right (declared order).
    * **after_* hooks**: run right-to-left (symmetric unwinding).
    * **wrap_* hooks**: onion-style -- each middleware wraps the next,
      innermost is the actual call.

    Exceptions are logged (``MIDDLEWARE_HOOK_ERROR``) and re-raised.
    The classification pipeline handles error disposition.

    Args:
        middleware: Ordered tuple of middleware instances.
    """

    __slots__ = ("_middleware",)

    def __init__(
        self,
        middleware: tuple[AgentMiddleware, ...] = (),
    ) -> None:
        middleware = tuple(middleware)
        names = [mw.name for mw in middleware]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            from synthorg.engine.middleware.errors import (  # noqa: PLC0415
                MiddlewareConfigError,
            )
            from synthorg.observability.events.middleware import (  # noqa: PLC0415
                MIDDLEWARE_DUPLICATE_CHAIN,
            )

            msg = f"Duplicate middleware names in chain: {sorted(set(dupes))}"
            logger.warning(
                MIDDLEWARE_DUPLICATE_CHAIN,
                message=msg,
                duplicates=sorted(set(dupes)),
            )
            raise MiddlewareConfigError(msg)
        self._middleware = middleware

    @property
    def middleware(self) -> tuple[AgentMiddleware, ...]:
        """Return the ordered middleware tuple."""
        return self._middleware

    @property
    def names(self) -> tuple[NotBlankStr, ...]:
        """Return middleware names in declared order."""
        return tuple(mw.name for mw in self._middleware)

    def __len__(self) -> int:
        """Return the number of middleware in the chain."""
        return len(self._middleware)

    async def run_before_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Run ``before_agent`` hooks left-to-right.

        Returns:
            The context after every middleware's ``before_agent`` hook
            has run; each hook's return value is threaded into the
            next.
        """
        for mw in self._middleware:
            logger.debug(
                MIDDLEWARE_BEFORE_AGENT,
                middleware=mw.name,
            )
            try:
                ctx = await mw.before_agent(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_HOOK_ERROR,
                    middleware=mw.name,
                    hook="before_agent",
                )
                raise
        return ctx

    async def run_before_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Run ``before_model`` hooks left-to-right.

        Returns:
            The context after every middleware's ``before_model``
            hook has run; each hook's return value is threaded into
            the next.
        """
        for mw in self._middleware:
            logger.debug(
                MIDDLEWARE_BEFORE_MODEL,
                middleware=mw.name,
            )
            try:
                ctx = await mw.before_model(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_HOOK_ERROR,
                    middleware=mw.name,
                    hook="before_model",
                )
                raise
        return ctx

    async def run_after_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Run ``after_model`` hooks right-to-left.

        Returns:
            The context after every middleware's ``after_model`` hook
            has run in reverse order; each hook's return value is
            threaded into the next.
        """
        for mw in reversed(self._middleware):
            logger.debug(
                MIDDLEWARE_AFTER_MODEL,
                middleware=mw.name,
            )
            try:
                ctx = await mw.after_model(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_HOOK_ERROR,
                    middleware=mw.name,
                    hook="after_model",
                )
                raise
        return ctx

    async def run_after_agent(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Run ``after_agent`` hooks right-to-left.

        Returns:
            The context after every middleware's ``after_agent`` hook
            has run in reverse order; each hook's return value is
            threaded into the next.
        """
        for mw in reversed(self._middleware):
            logger.debug(
                MIDDLEWARE_AFTER_AGENT,
                middleware=mw.name,
            )
            try:
                ctx = await mw.after_agent(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_HOOK_ERROR,
                    middleware=mw.name,
                    hook="after_agent",
                )
                raise
        return ctx

    async def run_wrap_model_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ModelCallable,
    ) -> ModelCallResult:
        """Run ``wrap_model_call`` hooks in onion order.

        Builds a nested chain where each middleware wraps the next.
        The innermost callable is the actual model call.

        Returns:
            The :class:`ModelCallResult` produced by the innermost
            ``call`` after every wrapper has run.
        """
        logger.debug(
            MIDDLEWARE_WRAP_MODEL_CALL,
            chain_length=len(self._middleware),
        )
        wrapped = call
        for mw in reversed(self._middleware):
            wrapped = _make_model_wrapper(mw, wrapped)
        try:
            return await wrapped(ctx)
        except Exception:
            logger.warning(
                MIDDLEWARE_HOOK_ERROR,
                hook="wrap_model_call",
                chain_length=len(self._middleware),
                chain_names=self.names,
            )
            raise

    async def run_wrap_tool_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        """Run ``wrap_tool_call`` hooks in onion order.

        Builds a nested chain where each middleware wraps the next.
        The innermost callable is the actual tool call.

        Returns:
            The :class:`ToolCallResult` produced by the innermost
            ``call`` after every wrapper has run.
        """
        logger.debug(
            MIDDLEWARE_WRAP_TOOL_CALL,
            chain_length=len(self._middleware),
        )
        wrapped = call
        for mw in reversed(self._middleware):
            wrapped = _make_tool_wrapper(mw, wrapped)
        try:
            return await wrapped(ctx)
        except Exception:
            logger.warning(
                MIDDLEWARE_HOOK_ERROR,
                hook="wrap_tool_call",
                chain_length=len(self._middleware),
                chain_names=self.names,
            )
            raise


# ── Wrapper factories (avoid closure variable capture issues) ─────


def _make_model_wrapper(
    mw: AgentMiddleware,
    inner: ModelCallable,
) -> ModelCallable:
    """Build an onion wrapper for a model call middleware.

    Returns:
        An async callable that invokes ``mw.wrap_model_call`` with
        ``inner`` as the wrapped target.
    """

    async def wrapper(ctx: AgentMiddlewareContext) -> ModelCallResult:
        return await mw.wrap_model_call(ctx, inner)

    return wrapper


def _make_tool_wrapper(
    mw: AgentMiddleware,
    inner: ToolCallable,
) -> ToolCallable:
    """Build an onion wrapper for a tool call middleware.

    Returns:
        An async callable that invokes ``mw.wrap_tool_call`` with
        ``inner`` as the wrapped target.
    """

    async def wrapper(ctx: AgentMiddlewareContext) -> ToolCallResult:
        return await mw.wrap_tool_call(ctx, inner)

    return wrapper
