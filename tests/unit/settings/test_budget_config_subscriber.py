"""Tests for ``BudgetConfigSettingsSubscriber``.

Three components hold their own copy of ``BudgetConfig`` and each was built
once at boot. A write adopted by one of them leaves the other two enforcing
the config the process started with, which is invisible: the write persists
and renders either way.
"""

from typing import NamedTuple
from unittest.mock import MagicMock, create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.optimizer import CostOptimizer
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.state import SettingsStateSlice
from synthorg.settings.subscribers.budget_config_subscriber import (
    BudgetConfigSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_BOOT_CURRENCY = "EUR"
_RESOLVED_CURRENCY = "GBP"


def _config(currency: str, total_monthly: float) -> BudgetConfig:
    return BudgetConfig(currency=currency, total_monthly=total_monthly)


class _Harness(NamedTuple):
    """The subscriber and everything a test asserts against it."""

    subscriber: BudgetConfigSettingsSubscriber
    app_state: AppState
    tracker: CostTracker
    optimizer: CostOptimizer
    resolver: MagicMock


def _subscriber(resolved: BudgetConfig) -> _Harness:
    boot = _config(_BOOT_CURRENCY, 100.0)
    tracker = CostTracker(budget_config=boot)
    optimizer = CostOptimizer(cost_tracker=tracker, budget_config=boot)
    app_state = make_app_state(config=RootConfig(company_name="test"))
    resolver = create_autospec(ConfigResolver, instance=True)
    resolver.get_budget_config.return_value = resolved
    app_state.wire(SettingsStateSlice, config_resolver=resolver)
    app_state.wire(
        BudgetStateSlice,
        budget_config=boot,
        cost_tracker=tracker,
        cost_optimizer=optimizer,
        budget_enforcer=BudgetEnforcer(budget_config=boot, cost_tracker=tracker),
    )
    sub = BudgetConfigSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return _Harness(sub, app_state, tracker, optimizer, resolver)


async def test_every_holder_of_the_config_adopts_the_write() -> None:
    """The enforcer is not the only component the write has to reach.

    The tracker's copy decides which currency it will accept a record in at
    all, so a currency change it never sees turns every subsequent record
    into a rejected one.
    """
    resolved = _config(_RESOLVED_CURRENCY, 250.0)
    harness = _subscriber(resolved)

    await harness.subscriber.on_settings_changed([("budget", "currency")])

    budget_slice = harness.app_state.slice(BudgetStateSlice)
    assert budget_slice.budget_config is resolved
    enforcer = budget_slice.budget_enforcer
    assert isinstance(enforcer, BudgetEnforcer)
    assert enforcer.budget_config is resolved
    assert harness.tracker.budget_config is resolved
    optimizer_config = harness.optimizer._budget_config
    assert optimizer_config is resolved


async def test_a_resolve_failure_restores_the_prior_config() -> None:
    resolved = _config(_RESOLVED_CURRENCY, 250.0)
    harness = _subscriber(resolved)
    harness.resolver.get_budget_config.side_effect = RuntimeError("resolver down")

    with pytest.raises(RuntimeError, match="resolver down"):
        await harness.subscriber.on_settings_changed([("budget", "currency")])

    prior = harness.app_state.slice(BudgetStateSlice).budget_config
    assert prior is not None
    assert prior.currency == _BOOT_CURRENCY
    # Nothing adopted a config the resolve never produced.
    assert harness.tracker.budget_config is prior
