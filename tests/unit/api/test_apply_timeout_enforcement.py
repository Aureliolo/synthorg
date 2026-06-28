"""Tests for the ``_apply_timeout_enforcement`` startup helper.

The boot apply path resolves ``engine.timeout_enforcement_enabled`` and
pushes it into the engine's process-global enforcement cache. A resolver
outage forces the cache back to ``True`` so a deployment can never be
left with timeout enforcement silently disabled.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.config_apply import (
    _apply_timeout_enforcement,
)
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.engine import timeout_enforcement
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_state(*, config_resolver: ConfigResolver) -> AppState:
    return make_app_state(
        config=RootConfig(company_name="test"),
        approval_store=ApprovalStore(),
        config_resolver=config_resolver,
    )


@pytest.fixture(autouse=True)
def _restore_enforcement_flag() -> object:
    """Restore the process-global flag after each test."""
    original = timeout_enforcement.is_timeout_enforcement_enabled()
    yield
    timeout_enforcement.set_timeout_enforcement_enabled(value=original)


class TestApplyTimeoutEnforcement:
    """Resolves ``engine.timeout_enforcement_enabled`` into the cache."""

    async def test_disabled_value_flips_cache_off(self) -> None:
        timeout_enforcement.set_timeout_enforcement_enabled(value=True)
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        state = _make_state(config_resolver=resolver)

        await _apply_timeout_enforcement(state)

        resolver.get_bool.assert_awaited_once_with(
            "engine",
            "timeout_enforcement_enabled",
        )
        assert timeout_enforcement.is_timeout_enforcement_enabled() is False

    async def test_enabled_value_flips_cache_on(self) -> None:
        timeout_enforcement.set_timeout_enforcement_enabled(value=False)
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = True
        state = _make_state(config_resolver=resolver)

        await _apply_timeout_enforcement(state)

        assert timeout_enforcement.is_timeout_enforcement_enabled() is True

    async def test_resolver_outage_forces_enforcement_on(self) -> None:
        # A resolver that had already served ``False`` on a prior request
        # must not leave enforcement off when this branch fails.
        timeout_enforcement.set_timeout_enforcement_enabled(value=False)
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = ValueError("settings backend down")
        state = _make_state(config_resolver=resolver)

        await _apply_timeout_enforcement(state)

        assert timeout_enforcement.is_timeout_enforcement_enabled() is True
