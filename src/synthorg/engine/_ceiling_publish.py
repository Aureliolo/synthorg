"""Publish a budget checker's ceilings onto an ``AgentContext``.

Shared by the fresh-run, approval-resume and checkpoint-resume paths so the
translation between ``SessionCeilings``'s "disabled" (0) and
``AgentContext``'s ``None`` lives in exactly one place.
"""

# module-kind: code

from synthorg.budget.session_budget import SessionBudgetChecker
from synthorg.engine.context import AgentContext


def ctx_ceiling_values(
    budget_checker: SessionBudgetChecker | None,
) -> tuple[float | None, int | None]:
    """``SessionCeilings``'s "disabled" (0) as ``AgentContext``'s ``None``.

    A verbatim stamp of a genuinely-zero money bound (a flat-rate
    connection) would fail ``AgentContext``'s own ``gt=0`` validation; a
    ``None`` checker (every bound disabled) reads the same as a checker
    whose ceilings are both disabled.

    Returns:
        ``(cost_ceiling, token_ceiling)``.
    """
    return (
        budget_checker.ceilings.as_optionals()
        if budget_checker is not None
        else (None, None)
    )


def sync_ctx_ceilings(
    ctx: AgentContext,
    budget_checker: SessionBudgetChecker | None,
) -> AgentContext:
    """Publish *budget_checker*'s ceilings onto *ctx*.

    A ceiling raised, lowered, or disabled entirely while a run sat parked
    -- approval or checkpoint -- is picked up by the checker rebuilt for
    the resumed dispatch, but not by the restored context, so *ctx* is
    synced to match, including the disabled case, where a cleared checker
    must clear *ctx*'s ceilings rather than leave it carrying the parked
    run's stale ones.

    Returns:
        *ctx*, with ``cost_ceiling``/``token_ceiling`` matching
        *budget_checker*.
    """
    cost_ceiling, token_ceiling = ctx_ceiling_values(budget_checker)
    return ctx.model_copy(
        update={"cost_ceiling": cost_ceiling, "token_ceiling": token_ceiling}
    )


__all__ = ["ctx_ceiling_values", "sync_ctx_ceilings"]
