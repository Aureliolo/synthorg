"""The budget every surface measures against is the configured one, from boot.

Three components hold their own ``BudgetConfig``, and all three are built
during construction, before persistence is connected and before any setting
can be read.
The settings subscriber hands a write to all three, so a config CHANGED while
the process runs lands everywhere. A config that was already stored when the
process started changes nothing, so it landed nowhere: a live deployment with
a configured monthly budget of 500 reported its remaining budget as 100, the
code default, for the whole life of the process.
"""

from unittest.mock import MagicMock, create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.budget.adoption import adopt_resolved_budget_config
from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.optimizer import CostOptimizer
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

#: What ``BudgetConfig`` defaults to, which is what construction builds from.
_BOOT_TOTAL = 100.0

#: What the operator configured and every surface has to measure against.
_CONFIGURED_TOTAL = 500.0


def _booted(resolver: MagicMock) -> tuple[AppState, CostTracker, CostOptimizer]:
    """An app state as construction leaves it: every holder on the defaults."""
    boot = BudgetConfig(total_monthly=_BOOT_TOTAL)
    tracker = CostTracker(budget_config=boot)
    optimizer = CostOptimizer(cost_tracker=tracker, budget_config=boot)
    app_state = make_app_state(config=RootConfig(company_name="test"))
    app_state.wire(SettingsStateSlice, config_resolver=resolver)
    app_state.wire(
        BudgetStateSlice,
        budget_config=boot,
        cost_tracker=tracker,
        cost_optimizer=optimizer,
        budget_enforcer=BudgetEnforcer(budget_config=boot, cost_tracker=tracker),
    )
    return app_state, tracker, optimizer


def _resolver(resolved: BudgetConfig | Exception) -> MagicMock:
    resolver: MagicMock = create_autospec(ConfigResolver, instance=True)
    if isinstance(resolved, Exception):
        resolver.get_budget_config.side_effect = resolved
    else:
        resolver.get_budget_config.return_value = resolved
    return resolver


async def test_boot_adopts_the_stored_config_on_every_holder() -> None:
    """Boot is the first pass, not a special case that skips the readers."""
    configured = BudgetConfig(total_monthly=_CONFIGURED_TOTAL)
    app_state, tracker, optimizer = _booted(_resolver(configured))

    adopted = await adopt_resolved_budget_config(app_state)

    assert adopted is configured
    budget_slice = app_state.slice(BudgetStateSlice)
    assert budget_slice.budget_config is configured
    enforcer = budget_slice.budget_enforcer
    assert isinstance(enforcer, BudgetEnforcer)
    assert enforcer.budget_config is configured
    assert tracker.budget_config is configured
    assert optimizer._budget_config is configured


async def test_a_resolve_failure_leaves_the_boot_config_standing() -> None:
    """Failing the boot over an unreadable budget would cost the whole stack.

    The ceiling that stands is then the code default, which is lower than any
    operator would choose, so the failure mode refuses spend rather than
    allowing it.
    """
    app_state, tracker, _ = _booted(_resolver(RuntimeError("settings unreadable")))

    adopted = await adopt_resolved_budget_config(app_state)

    assert adopted is None
    standing = tracker.budget_config
    assert standing is not None
    assert standing.total_monthly == _BOOT_TOTAL


async def test_no_resolver_adopts_nothing() -> None:
    """A harness runs without settings at all and must still start."""
    boot = BudgetConfig(total_monthly=_BOOT_TOTAL)
    app_state = make_app_state(config=RootConfig(company_name="test"))
    app_state.wire(BudgetStateSlice, budget_config=boot)

    assert await adopt_resolved_budget_config(app_state) is None
