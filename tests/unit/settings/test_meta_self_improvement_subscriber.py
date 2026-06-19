"""Tests for ``MetaSelfImprovementSettingsSubscriber``.

The subscriber invalidates the cached ``SelfImprovementConfig`` on the
meta slice when an operator edits ``meta.self_improvement``. Tests cover
protocol conformance, the watched-key set matching the registered
setting, end-to-end invalidation (a populated cache field is wired back
to ``None``), and ``MemoryError`` propagation through the error path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.state import MetaStateSlice, self_improvement_config_of
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.meta_self_improvement_subscriber import (
    MetaSelfImprovementSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_subscriber() -> tuple[MetaSelfImprovementSettingsSubscriber, AppState]:
    """Build a subscriber with a real AppState + ``{}``-resolving service."""
    entry = SimpleNamespace(value="{}")
    service = mock_of[SettingsService](get=AsyncMock(return_value=entry))
    app_state = make_app_state(settings_service=service)
    sub = MetaSelfImprovementSettingsSubscriber(
        app_state=app_state,
        settings_service=service,
    )
    return sub, app_state


class TestSubscriberProtocol:
    """Conforms to ``SettingsSubscriber`` and watches the right key."""

    def test_isinstance_check(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.watched_keys == frozenset({("meta", "self_improvement")})

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "meta-self-improvement"


class TestInvalidation:
    """``on_settings_changed`` clears the cached config field."""

    async def test_invalidation_clears_cached_config(self) -> None:
        sub, app_state = _make_subscriber()

        # Populate the cache via the accessor, then invalidate.
        cached = await self_improvement_config_of(app_state)
        assert isinstance(cached, SelfImprovementConfig)
        assert app_state.slice(MetaStateSlice).self_improvement_config is cached

        await sub.on_settings_changed("meta", "self_improvement")

        assert app_state.slice(MetaStateSlice).self_improvement_config is None

    async def test_invalidation_noop_when_already_empty(self) -> None:
        sub, app_state = _make_subscriber()

        await sub.on_settings_changed("meta", "self_improvement")

        assert app_state.slice(MetaStateSlice).self_improvement_config is None


class TestErrorPath:
    """Critical errors propagate; the wire seam is exercised."""

    async def test_memory_error_propagates(self) -> None:
        sub, _ = _make_subscriber()
        boom = create_autospec(AppState, instance=True)
        boom.wire.side_effect = MemoryError()
        sub._app_state = boom

        with pytest.raises(MemoryError):
            await sub.on_settings_changed("meta", "self_improvement")
