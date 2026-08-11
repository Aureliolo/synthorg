"""A run has a bound even where money measures nothing.

Against a provider that bills by flat subscription, cost stays 0.0 for the
life of the run: the money ceiling can never fire, and the only remaining
limit is the turn budget (300 turns with three extensions, so up to 1200).
Tokens are counted on every provider, billed or not, so the token ceiling is
the same backstop in the unit that is always available.
"""

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.errors import RunHardTokenCeilingExceededError
from synthorg.budget.session_budget import (
    SessionCeilings,
    build_session_budget_checker,
)
from synthorg.budget.tracker import CostTracker
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.providers.models import TokenUsage
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


class _Ctx:
    """Minimal structural stand-in for the run context a checker reads."""

    def __init__(self, *, cost: float, tokens: int) -> None:
        self.accumulated_cost = TokenUsage(
            input_tokens=tokens,
            output_tokens=0,
            cost=cost,
        )


def _config(*, token_ceiling: int, money_ceiling: float = 0.0) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=0.0,
        alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        per_agent_daily_limit=0.0,
        per_task_limit=0.0,
        run_hard_ceiling=money_ceiling,
        run_hard_token_ceiling=token_ceiling,
    )


def _task(**overrides: object) -> Task:
    return Task(
        id=as_uuid("task-token"),
        title="Ship it",
        description="Deliver the slice.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
        budget_limit=0.0,
        **overrides,  # type: ignore[arg-type]
    )


class TestRunCeiling:
    async def test_a_flat_rate_run_still_halts(self) -> None:
        # cost stays 0.0 forever, so only the token branch can fire.
        from synthorg.budget.enforcer import BudgetEnforcer

        cfg = _config(token_ceiling=1_000)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=CostTracker(budget_config=cfg),
        )
        checker = await enforcer.make_budget_checker(_task(), "agent-1")

        assert checker is not None
        with pytest.raises(RunHardTokenCeilingExceededError) as caught:
            checker(_Ctx(cost=0.0, tokens=1_000))  # type: ignore[arg-type]
        assert caught.value.token_ceiling == 1_000
        assert caught.value.tokens_used == 1_000

    async def test_below_the_ceiling_does_not_halt(self) -> None:
        from synthorg.budget.enforcer import BudgetEnforcer

        cfg = _config(token_ceiling=1_000)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=CostTracker(budget_config=cfg),
        )
        checker = await enforcer.make_budget_checker(_task(), "agent-1")

        assert checker is not None
        assert checker(_Ctx(cost=0.0, tokens=999)) is False  # type: ignore[arg-type]

    async def test_the_task_overrides_the_global(self) -> None:
        from synthorg.budget.enforcer import BudgetEnforcer

        cfg = _config(token_ceiling=1_000_000)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=CostTracker(budget_config=cfg),
        )
        checker = await enforcer.make_budget_checker(
            _task(hard_token_ceiling=10), "agent-1"
        )

        assert checker is not None
        with pytest.raises(RunHardTokenCeilingExceededError):
            checker(_Ctx(cost=0.0, tokens=10))  # type: ignore[arg-type]

    async def test_zero_is_the_explicit_opt_out(self) -> None:
        from synthorg.budget.enforcer import BudgetEnforcer

        cfg = _config(token_ceiling=0)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=CostTracker(budget_config=cfg),
        )

        assert await enforcer.make_budget_checker(_task(), "agent-1") is None


class TestSessionCeiling:
    def test_no_bound_at_all_returns_none(self) -> None:
        # None rather than a never-true predicate, so a caller can tell
        # "no bound" from "a bound not yet reached".
        assert build_session_budget_checker(cost_ceiling=0.0, token_ceiling=0) is None

    def test_tokens_bind_where_money_cannot(self) -> None:
        checker = build_session_budget_checker(cost_ceiling=1.0, token_ceiling=500)
        assert checker is not None
        # A flat-rate session: real work, zero cost.
        assert checker(_Ctx(cost=0.0, tokens=500)) is True
        assert checker(_Ctx(cost=0.0, tokens=499)) is False

    def test_money_still_binds_on_a_metered_session(self) -> None:
        checker = build_session_budget_checker(cost_ceiling=1.0, token_ceiling=0)
        assert checker is not None
        assert checker(_Ctx(cost=1.0, tokens=1)) is True
        assert checker(_Ctx(cost=0.5, tokens=1)) is False

    def test_the_two_bounds_travel_together(self) -> None:
        # Paired so a wiring path cannot carry one and drop the other.
        ceilings = SessionCeilings(cost_ceiling=2.0, token_ceiling=1_000)
        checker = build_session_budget_checker(
            cost_ceiling=ceilings.cost_ceiling,
            token_ceiling=ceilings.token_ceiling,
        )
        assert checker is not None
        assert checker(_Ctx(cost=0.0, tokens=1_000)) is True
