"""The budget every surface measures against is the configured one, from boot.

Four components hold their own ``BudgetConfig``, each built before any
setting can be read. The settings subscriber hands a write to all of them,
so a config CHANGED while the process runs lands everywhere. A config that
was already stored when the process started changes nothing, so it landed
nowhere: a deployment with a configured monthly budget of 500 reports its
remaining budget as 100, the code default, for the whole life of the
process.

The second half is that adoption has to be the LAST answer as well as the
first. A later boot step minting its own ``BudgetConfig()`` puts the slice
and the enforcer back on the default while the gauge captured at adoption
still shows the operator's number, which reads as fixed and enforces as
though it never was.
"""

from unittest.mock import MagicMock, create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.budget.adoption import (
    adopt_resolved_budget_config,
    resolved_budget_config,
)
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


def test_later_boot_wiring_does_not_mint_a_config_over_the_adopted_one() -> None:
    """Nothing downstream of adoption may re-answer which ceiling applies.

    Adoption runs in the first subsystem-reconcile pass; the cost dial is
    wired after it, and a fresh ``BudgetConfig()`` built there would land on
    the slice and on a rebuilt enforcer, putting both back on the code
    default while the gauge captured at adoption still reads the operator's
    number. The slice holds the answer, so the later step reads it.
    """
    configured = BudgetConfig(total_monthly=_CONFIGURED_TOTAL)
    app_state = make_app_state(config=RootConfig(company_name="test"))
    app_state.wire(BudgetStateSlice, budget_config=configured)

    assert resolved_budget_config(app_state) is configured


def test_a_boot_with_nothing_adopted_falls_back_to_the_default() -> None:
    """A persistence-less boot has no adopted config and must still wire."""
    app_state = make_app_state(config=RootConfig(company_name="test"))

    assert (
        resolved_budget_config(app_state).total_monthly == BudgetConfig().total_monthly
    )
