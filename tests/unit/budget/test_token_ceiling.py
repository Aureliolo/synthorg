"""A run has a bound even where money measures nothing.

Against a provider that bills by flat subscription, cost stays 0.0 for the
life of the run: the money ceiling can never fire, and the only remaining
limit is the turn budget (300 turns with three extensions, so up to 1200).
Tokens are counted on every provider, billed or not, so the token ceiling is
the same backstop in the unit that is always available.
"""

from typing import Final

import pytest

from synthorg.api.exception_handlers import build_error_detail
from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import (
    RunHardCeilingExceededError,
    RunHardTokenCeilingExceededError,
)
from synthorg.budget.session_budget import (
    SessionCeilings,
    build_session_budget_checker,
)
from synthorg.budget.tracker import CostTracker
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.providers.models import TokenUsage
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

_BUDGET_EXHAUSTED_STATUS: Final[int] = 402


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
        cfg = _config(token_ceiling=1_000)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=CostTracker(budget_config=cfg),
        )
        checker = await enforcer.make_budget_checker(_task(), "agent-1")

        assert checker is not None
        with pytest.raises(RunHardTokenCeilingExceededError) as caught:
            checker(_Ctx(cost=0.0, tokens=1_000))
        assert caught.value.token_ceiling == 1_000
        assert caught.value.tokens_used == 1_000

    async def test_the_crossing_reaches_a_client_under_its_own_code(self) -> None:
        """The halt has to be distinguishable on the wire, not just in-process.

        The error inherits its whole serialisation from
        ``BudgetExhaustedError``, so nothing in the class body would fail if
        the code were dropped or aliased onto the money sibling's; a client
        would then see a token halt reported as a money one and raise the
        ceiling that was never crossed.
        """
        cfg = _config(token_ceiling=1_000)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=CostTracker(budget_config=cfg),
        )
        checker = await enforcer.make_budget_checker(_task(), "agent-1")

        assert checker is not None
        with pytest.raises(RunHardTokenCeilingExceededError) as caught:
            checker(_Ctx(cost=0.0, tokens=1_000))

        exc = caught.value
        assert exc.error_code is ErrorCode.RUN_HARD_TOKEN_CEILING_EXCEEDED
        assert exc.error_code is not RunHardCeilingExceededError.error_code

        # The one serialiser both wire paths share: the RFC 9457 handler and
        # the streaming error frame each read these same normalised fields,
        # so asserting the detail covers the ProblemDetail body too.
        detail = build_error_detail(exc)
        assert detail.error_code == ErrorCode.RUN_HARD_TOKEN_CEILING_EXCEEDED.value
        assert detail.error_category == ErrorCategory.BUDGET_EXHAUSTED.value
        assert detail.retryable is False
        assert exc.status_code == _BUDGET_EXHAUSTED_STATUS

    async def test_below_the_ceiling_does_not_halt(self) -> None:
        cfg = _config(token_ceiling=1_000)
        enforcer = BudgetEnforcer(
            budget_config=cfg,
            cost_tracker=CostTracker(budget_config=cfg),
        )
        checker = await enforcer.make_budget_checker(_task(), "agent-1")

        assert checker is not None
        assert checker(_Ctx(cost=0.0, tokens=999)) is False

    async def test_the_task_overrides_the_global(self) -> None:
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
            checker(_Ctx(cost=0.0, tokens=10))

    async def test_zero_is_the_explicit_opt_out(self) -> None:
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
        assert (
            build_session_budget_checker(
                SessionCeilings(cost_ceiling=0.0, token_ceiling=0)
            )
            is None
        )

    def test_tokens_bind_where_money_cannot(self) -> None:
        checker = build_session_budget_checker(
            SessionCeilings(cost_ceiling=1.0, token_ceiling=500)
        )
        assert checker is not None
        # A flat-rate session: real work, zero cost.
        assert checker(_Ctx(cost=0.0, tokens=500)) is True
        assert checker(_Ctx(cost=0.0, tokens=499)) is False

    def test_money_still_binds_on_a_metered_session(self) -> None:
        checker = build_session_budget_checker(
            SessionCeilings(cost_ceiling=1.0, token_ceiling=0)
        )
        assert checker is not None
        assert checker(_Ctx(cost=1.0, tokens=1)) is True
        assert checker(_Ctx(cost=0.5, tokens=1)) is False

    def test_the_two_bounds_travel_together(self) -> None:
        # The builder takes the pair, so a caller holding one bound has to
        # say what the other is rather than being able to omit it.
        checker = build_session_budget_checker(
            SessionCeilings(cost_ceiling=2.0, token_ceiling=1_000)
        )
        assert checker is not None
        assert checker(_Ctx(cost=0.0, tokens=1_000)) is True

    @pytest.mark.parametrize(
        ("cost_ceiling", "token_ceiling"),
        [(None, None), (-1.0, -1), (0.0, None), (None, 0)],
    )
    def test_unset_and_non_positive_both_mean_disabled(
        self,
        cost_ceiling: float | None,
        token_ceiling: int | None,
    ) -> None:
        # `of` is where the optionals collapse, so a caller with nothing
        # configured produces a pair rather than a special case.
        ceilings = SessionCeilings.of(
            cost_ceiling=cost_ceiling, token_ceiling=token_ceiling
        )
        assert ceilings.bounded is False
        assert build_session_budget_checker(ceilings) is None
