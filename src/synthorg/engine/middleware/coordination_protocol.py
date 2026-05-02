"""Coordination-level middleware protocol and composition chain.

Defines the five-hook ``CoordinationMiddleware`` protocol operating
on ``CoordinationMiddlewareContext`` (distinct from the agent-level
``AgentMiddlewareContext``).  Coordination middleware hooks into the
``decompose -> route -> dispatch -> rollup -> update parent`` pipeline.
"""

import copy
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.coordination.models import (
    CoordinationContext,  # noqa: TC001
    CoordinationPhaseResult,  # noqa: TC001
)
from synthorg.engine.middleware.models import (
    ProgressLedger,  # noqa: TC001
    TaskLedger,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.middleware import (
    MIDDLEWARE_COORDINATION_HOOK_ERROR,
)

logger = get_logger(__name__)


# ── Coordination middleware context ───────────────────────────────


class CoordinationMiddlewareContext(BaseModel):
    """Execution state carried through the coordination middleware chain.

    Fields are populated progressively as the coordination pipeline
    advances: ``decomposition_result`` is ``None`` during
    ``before_decompose``, set after ``after_decompose``, etc.

    Attributes:
        coordination_context: Original coordination input (task, agents, config).
        decomposition_result: Result of task decomposition (set after phase 1).
        routing_result: Result of task routing (set after phase 2).
        dispatch_result: Result of dispatch execution (set after phase 5).
        status_rollup: Aggregated subtask status rollup (set after phase 6).
        phases: Phase results accumulated so far.
        task_ledger: TaskLedger populated by TaskLedgerMiddleware.
        progress_ledger: ProgressLedger populated by ProgressLedgerMiddleware.
        metadata: Middleware-to-middleware data pass-through.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    coordination_context: CoordinationContext = Field(
        description="Original coordination input",
    )
    decomposition_result: Any | None = Field(
        default=None,
        description="DecompositionResult (set after phase 1)",
    )
    routing_result: Any | None = Field(
        default=None,
        description="RoutingResult (set after phase 2)",
    )
    dispatch_result: Any | None = Field(
        default=None,
        description="DispatchResult (set after phase 5)",
    )
    status_rollup: Any | None = Field(
        default=None,
        description="SubtaskStatusRollup (set after phase 6)",
    )
    phases: tuple[CoordinationPhaseResult, ...] = Field(
        default=(),
        description="Phase results accumulated so far",
    )
    task_ledger: TaskLedger | None = Field(
        default=None,
        description="TaskLedger from TaskLedgerMiddleware",
    )
    progress_ledger: ProgressLedger | None = Field(
        default=None,
        description="ProgressLedger from ProgressLedgerMiddleware",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Middleware-to-middleware data pass-through",
    )

    @model_validator(mode="after")
    def _deepcopy_metadata(self) -> CoordinationMiddlewareContext:
        """Defensive copy so callers cannot mutate the frozen model."""
        object.__setattr__(
            self,
            "metadata",
            copy.deepcopy(self.metadata),
        )
        return self

    def with_metadata(
        self,
        key: str,
        value: Any,
    ) -> CoordinationMiddlewareContext:
        """Return a copy with an additional metadata entry."""
        updated = copy.deepcopy(self.metadata)
        updated[key] = copy.deepcopy(value)
        return self.model_copy(update={"metadata": updated})


# ── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class CoordinationMiddleware(Protocol):
    """Coordination-level middleware with five async hooks.

    Hooks map to the coordination pipeline phases:

    * ``before_decompose`` -- before Phase 1 (decomposition)
    * ``after_decompose`` -- after Phase 1, before Phase 2 (routing)
    * ``before_dispatch`` -- before Phase 3-5 (dispatch)
    * ``after_rollup`` -- after Phase 6 (rollup)
    * ``before_update_parent`` -- before Phase 7 (parent task update)

    All hooks run linearly in declared (left-to-right) order.
    Coordination middleware is a phase-based pipeline, not a stack.
    """

    @property
    def name(self) -> NotBlankStr:
        """Unique middleware name for registry and logging."""
        ...

    async def before_decompose(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Before task decomposition."""
        ...

    async def after_decompose(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """After decomposition, before routing."""
        ...

    async def before_dispatch(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Before dispatch (plan review gate slot)."""
        ...

    async def after_rollup(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """After rollup (progress ledger / replan slot)."""
        ...

    async def before_update_parent(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Before parent task status update."""
        ...


# ── Base class with no-op defaults ────────────────────────────────


class BaseCoordinationMiddleware:
    """Base class providing no-op defaults for all five hooks.

    Subclass and override only the hooks you need.

    Args:
        name: Unique middleware name for registry and logging.
    """

    __slots__ = ("_name",)

    def __init__(self, *, name: str) -> None:
        if not name or not name.strip():
            msg = "middleware name cannot be blank"
            logger.warning(
                MIDDLEWARE_COORDINATION_HOOK_ERROR,
                error=msg,
                name=repr(name),
            )
            raise ValueError(msg)
        self._name: NotBlankStr = name.strip()

    @property
    def name(self) -> NotBlankStr:
        """Unique middleware name."""
        return self._name

    async def before_decompose(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """No-op: return context unchanged."""
        return ctx

    async def after_decompose(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """No-op: return context unchanged."""
        return ctx

    async def before_dispatch(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """No-op: return context unchanged."""
        return ctx

    async def after_rollup(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """No-op: return context unchanged."""
        return ctx

    async def before_update_parent(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """No-op: return context unchanged."""
        return ctx


# ── Composition chain ─────────────────────────────────────────────


class CoordinationMiddlewareChain:
    """Composes an ordered tuple of ``CoordinationMiddleware`` instances.

    Composition rules:

    * All hooks run left-to-right (declared order). Coordination
      middleware is a linear phase-based pipeline, not a stack.

    No onion wrapping -- coordination hooks are linear transforms
    on the context, not call wrappers.

    Args:
        middleware: Ordered tuple of middleware instances.
    """

    __slots__ = ("_middleware",)

    def __init__(
        self,
        middleware: tuple[CoordinationMiddleware, ...] = (),
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
    def middleware(self) -> tuple[CoordinationMiddleware, ...]:
        """Return the ordered middleware tuple."""
        return self._middleware

    @property
    def names(self) -> tuple[NotBlankStr, ...]:
        """Return middleware names in declared order."""
        return tuple(mw.name for mw in self._middleware)

    def __len__(self) -> int:
        """Return the number of middleware in the chain."""
        return len(self._middleware)

    async def run_before_decompose(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Run ``before_decompose`` hooks left-to-right."""
        for mw in self._middleware:
            try:
                ctx = await mw.before_decompose(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_COORDINATION_HOOK_ERROR,
                    middleware=mw.name,
                    hook="before_decompose",
                )
                raise
        return ctx

    async def run_after_decompose(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Run ``after_decompose`` hooks left-to-right."""
        for mw in self._middleware:
            try:
                ctx = await mw.after_decompose(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_COORDINATION_HOOK_ERROR,
                    middleware=mw.name,
                    hook="after_decompose",
                )
                raise
        return ctx

    async def run_before_dispatch(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Run ``before_dispatch`` hooks left-to-right."""
        for mw in self._middleware:
            try:
                ctx = await mw.before_dispatch(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_COORDINATION_HOOK_ERROR,
                    middleware=mw.name,
                    hook="before_dispatch",
                )
                raise
        return ctx

    async def run_after_rollup(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Run ``after_rollup`` hooks left-to-right."""
        for mw in self._middleware:
            try:
                ctx = await mw.after_rollup(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_COORDINATION_HOOK_ERROR,
                    middleware=mw.name,
                    hook="after_rollup",
                )
                raise
        return ctx

    async def run_before_update_parent(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Run ``before_update_parent`` hooks left-to-right."""
        for mw in self._middleware:
            try:
                ctx = await mw.before_update_parent(ctx)
            except Exception:
                logger.warning(
                    MIDDLEWARE_COORDINATION_HOOK_ERROR,
                    middleware=mw.name,
                    hook="before_update_parent",
                )
                raise
        return ctx
