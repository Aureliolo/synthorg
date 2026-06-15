"""Tests for the ``create_replan_hook`` factory."""

import pytest

from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.engine.middleware.coordination_constraints import (
    MagenticReplanHook,
    NoOpReplanHook,
)
from synthorg.engine.middleware.replan_factory import create_replan_hook
from tests._shared import mock_of


@pytest.mark.unit
class TestCreateReplanHook:
    """create_replan_hook maps a discriminator to a replan hook."""

    def test_noop_is_safe_default(self) -> None:
        hook = create_replan_hook("noop", max_stall_count=3, max_reset_count=2)
        assert isinstance(hook, NoOpReplanHook)

    def test_magentic_threads_caps(self) -> None:
        hook = create_replan_hook("magentic", max_stall_count=7, max_reset_count=4)
        assert isinstance(hook, MagenticReplanHook)
        assert hook._max_stall_count == 7
        assert hook._max_reset_count == 4

    def test_magentic_threads_budget_enforcer(self) -> None:
        enforcer = mock_of[BudgetEnforcer]()
        hook = create_replan_hook(
            "magentic",
            max_stall_count=3,
            max_reset_count=2,
            budget_enforcer=enforcer,
        )
        assert isinstance(hook, MagenticReplanHook)
        assert hook._budget_enforcer is enforcer

    def test_noop_ignores_caps_and_enforcer(self) -> None:
        enforcer = mock_of[BudgetEnforcer]()
        hook = create_replan_hook(
            "noop",
            max_stall_count=9,
            max_reset_count=9,
            budget_enforcer=enforcer,
        )
        assert isinstance(hook, NoOpReplanHook)

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            create_replan_hook("bogus", max_stall_count=3, max_reset_count=2)
