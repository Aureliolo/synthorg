"""Tests for the ``_apply_security_timeout_interval`` startup helper.

The hardcoded ``_DEFAULT_TIMEOUT_CHECK_INTERVAL_SECONDS`` baked into
``api/app.py`` only covers the registry-default leg of the
DB > env > YAML > default chain. After persistence connects and
``_apply_bridge_config`` wires the resolver, this helper pulls the
operator-tuned value and calls ``scheduler.reschedule(...)`` so the
configured cadence takes effect on the next tick without restart.
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers import _apply_security_timeout_interval
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.resolver import ConfigResolver

pytestmark = pytest.mark.unit


def _make_state(*, config_resolver: ConfigResolver | None) -> AppState:
    state = AppState(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
    )
    state._config_resolver = config_resolver
    return state


def _make_scheduler() -> ApprovalTimeoutScheduler:
    """Return a Mock that responds to ``reschedule(float)`` with ``spec=``."""
    return cast(
        "ApprovalTimeoutScheduler",
        MagicMock(spec=ApprovalTimeoutScheduler),
    )


class TestApplySecurityTimeoutInterval:
    """Resolves ``security.timeout_check_interval_seconds`` and reschedules."""

    async def test_no_scheduler_is_noop(self) -> None:
        state = _make_state(config_resolver=AsyncMock(spec=ConfigResolver))

        await _apply_security_timeout_interval(state, scheduler=None)

        # Resolver must NOT be hit when there's no scheduler to reschedule.
        cast("AsyncMock", state.config_resolver).get_float.assert_not_awaited()

    async def test_no_resolver_is_noop(self) -> None:
        scheduler = _make_scheduler()
        state = _make_state(config_resolver=None)

        await _apply_security_timeout_interval(state, scheduler=scheduler)

        cast("MagicMock", scheduler).reschedule.assert_not_called()

    async def test_resolves_and_reschedules(self) -> None:
        scheduler = _make_scheduler()
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_float.return_value = 12.5
        state = _make_state(config_resolver=resolver)

        await _apply_security_timeout_interval(state, scheduler=scheduler)

        resolver.get_float.assert_awaited_once_with(
            "security",
            "timeout_check_interval_seconds",
        )
        cast("MagicMock", scheduler).reschedule.assert_called_once_with(12.5)

    async def test_resolver_outage_leaves_scheduler_unchanged(self) -> None:
        scheduler = _make_scheduler()
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_float.side_effect = ValueError("settings backend down")
        state = _make_state(config_resolver=resolver)

        await _apply_security_timeout_interval(state, scheduler=scheduler)

        cast("MagicMock", scheduler).reschedule.assert_not_called()

    async def test_invalid_resolved_value_skipped_with_warning(self) -> None:
        scheduler = _make_scheduler()
        # ``ApprovalTimeoutScheduler.reschedule`` raises ValueError on a
        # non-positive interval. The helper must catch it so a bad
        # operator override cannot kill startup.
        cast("MagicMock", scheduler).reschedule.side_effect = ValueError(
            "interval_seconds must be positive, got -1.0",
        )
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_float.return_value = -1.0
        state = _make_state(config_resolver=resolver)

        await _apply_security_timeout_interval(state, scheduler=scheduler)

        cast("MagicMock", scheduler).reschedule.assert_called_once_with(-1.0)
