# module-kind: code
"""The middleware conversation, held apart from the pipeline that has it.

Five hooks run between the coordination phases, and each one was written
inline: a ``chain is not None`` test, a ``model_copy`` refreshing whichever
artefacts had appeared since the last hook, the call, and a read-back of
whatever the middleware replaced. Interleaved with the phases they sit
between, that is over fifty lines of bookkeeping inside a function whose
subject is the pipeline, and the phase order (the thing a reader comes to
that function for) is only visible between them.

The relay owns the running context and the absent-chain case, so the
pipeline states its phases and asks the relay in between. With no chain
wired every method returns its argument unchanged, which is what keeps the
test out of the caller.
"""

from typing import TYPE_CHECKING, Final

from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
)
from synthorg.engine.decomposition.models import (
    DecompositionResult,
)
from synthorg.engine.decomposition.status_rollup import SubtaskStatusRollup
from synthorg.engine.routing.models import RoutingResult

if TYPE_CHECKING:
    # Same cold-import cycle-breaker the service uses: importing the
    # middleware protocol at module level pulls the routing / decomposition
    # chain back through this package during a cold import.
    from synthorg.engine.middleware.coordination_protocol import (
        CoordinationMiddlewareChain,
        CoordinationMiddlewareContext,
    )

#: Metadata key by which an approved-plan resume tells ``before_dispatch``
#: the plan-review gate is already satisfied, so an approved plan is not
#: gated a second time.
_PLAN_REVIEW_APPROVED: Final[str] = "plan_review_approved"


class CoordinationMiddlewareRelay:
    """Runs the coordination hooks, or nothing when no chain is wired.

    Args:
        chain: The wired chain, or ``None`` to disable middleware entirely.
    """

    __slots__ = ("_chain", "_ctx")

    def __init__(self, chain: CoordinationMiddlewareChain | None) -> None:
        self._chain = chain
        self._ctx: CoordinationMiddlewareContext | None = None

    async def opened(
        self,
        context: CoordinationContext,
        *,
        plan_preapproved: bool,
    ) -> None:
        """Open the conversation for one coordination run.

        Args:
            context: The run's coordination input.
            plan_preapproved: Whether this run dispatches a plan a human
                already approved, in which case ``before_decompose`` does
                not run (there is nothing left to decompose) and the gate
                is marked satisfied for ``before_dispatch``.
        """
        if self._chain is None:
            return
        from synthorg.engine.middleware.coordination_protocol import (  # noqa: PLC0415
            CoordinationMiddlewareContext,
        )

        ctx = CoordinationMiddlewareContext(coordination_context=context)
        if plan_preapproved:
            self._ctx = ctx.with_metadata(
                _PLAN_REVIEW_APPROVED,
                True,  # noqa: FBT003 -- metadata value, not a flag param
            )
            return
        self._ctx = await self._chain.run_before_decompose(ctx)

    async def after_decompose(
        self,
        result: DecompositionResult,
        phases: list[CoordinationPhaseResult],
    ) -> DecompositionResult:
        """Offer the decomposed plan, and return whichever one dispatches.

        Returns:
            The middleware's replacement plan, or *result* unchanged.
        """
        chain = self._chain
        ctx = self._refreshed({"decomposition_result": result, "phases": tuple(phases)})
        if chain is None or ctx is None:
            return result
        self._ctx = await chain.run_after_decompose(ctx)
        replacement = self._ctx.decomposition_result
        return result if replacement is None else replacement

    async def before_dispatch(
        self,
        routing_result: RoutingResult,
        phases: list[CoordinationPhaseResult],
    ) -> RoutingResult:
        """Offer the routing, and return what the waves are built from.

        Runs before validation and topology resolution, so a middleware
        re-routing an unassigned subtask is reflected in both.

        Returns:
            The middleware's replacement routing, or *routing_result*
            unchanged.
        """
        chain = self._chain
        ctx = self._refreshed(
            {"routing_result": routing_result, "phases": tuple(phases)}
        )
        if chain is None or ctx is None:
            return routing_result
        self._ctx = await chain.run_before_dispatch(ctx)
        replacement = self._ctx.routing_result
        return routing_result if replacement is None else replacement

    async def after_rollup(
        self,
        dispatch_result: DispatchResult,
        rollup: SubtaskStatusRollup | None,
        phases: list[CoordinationPhaseResult],
    ) -> SubtaskStatusRollup | None:
        """Offer the dispatch outcome and its rollup.

        Returns:
            The rollup the parent update should act on, which is whatever
            the hook left: a middleware sanitising the rollup away to
            ``None`` is making a decision, not failing to make one.
        """
        chain = self._chain
        ctx = self._refreshed(
            {
                "dispatch_result": dispatch_result,
                "status_rollup": rollup,
                "phases": tuple(phases),
            }
        )
        if chain is None or ctx is None:
            return rollup
        self._ctx = await chain.run_after_rollup(ctx)
        return self._ctx.status_rollup

    async def before_update_parent(
        self,
        rollup: SubtaskStatusRollup | None,
    ) -> SubtaskStatusRollup | None:
        """Take a last look at the rollup before it moves the parent task.

        Returns:
            The rollup the parent update should act on.
        """
        chain, ctx = self._chain, self._ctx
        if chain is None or ctx is None:
            return rollup
        self._ctx = await chain.run_before_update_parent(ctx)
        return self._ctx.status_rollup

    def _refreshed(
        self, update: dict[str, object]
    ) -> CoordinationMiddlewareContext | None:
        """Carry the artefacts produced since the last hook into the context.

        Returns:
            The updated context, or ``None`` when no conversation is open.
        """
        if self._ctx is None:
            return None
        return self._ctx.model_copy(update=update)


__all__ = ["CoordinationMiddlewareRelay"]
