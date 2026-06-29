"""Tests for ``WsAuthLimitsSettingsSubscriber``.

Proves the hot-reload chain: a watched ``api.ws_*`` / ``auth_revalidate_*``
change resolves the new value and pushes it onto the live ``WsAuthLimits``
the ``/ws`` handler samples per connection-open, with no restart. Covers
protocol conformance, each watched key's live effect, resolver-failure
retains the prior value (and re-raises), and unexpected-pair no-op.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.ws_auth_limits_subscriber import (
    WsAuthLimitsSettingsSubscriber,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    float_return: float = 12.5,
    int_return: int = 99,
    float_side_effect: BaseException | None = None,
    int_side_effect: BaseException | None = None,
) -> tuple[WsAuthLimitsSettingsSubscriber, AppState]:
    """Build the subscriber over a real AppState + spec'd resolver."""
    resolver = create_autospec(ConfigResolver, instance=True)
    if float_side_effect is not None:
        resolver.get_float.side_effect = float_side_effect
    else:
        resolver.get_float.return_value = float_return
    if int_side_effect is not None:
        resolver.get_int.side_effect = int_side_effect
    else:
        resolver.get_int.return_value = int_return
    app_state = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
    )
    sub = WsAuthLimitsSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.watched_keys == frozenset(
            {
                ("api", "ws_auth_timeout_seconds"),
                ("api", "ws_frame_timeout_seconds"),
                ("api", "auth_revalidate_window_seconds"),
                ("api", "auth_revalidate_max_failures"),
            }
        )

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "ws-auth-limits"


class TestApply:
    async def test_auth_timeout_applies_live(self) -> None:
        sub, app_state = _make_subscriber(float_return=27.0)
        await sub.on_settings_changed("api", "ws_auth_timeout_seconds")
        assert app_state.ws_auth_limits.auth_timeout_seconds == 27.0

    @pytest.mark.parametrize(
        ("key", "getter"),
        [
            ("ws_frame_timeout_seconds", "frame_timeout_seconds"),
            ("auth_revalidate_window_seconds", "auth_revalidate_window_seconds"),
            ("auth_revalidate_max_failures", "auth_revalidate_max_failures"),
        ],
    )
    async def test_int_keys_apply_live(self, key: str, getter: str) -> None:
        sub, app_state = _make_subscriber(int_return=42)
        await sub.on_settings_changed("api", key)
        assert getattr(app_state.ws_auth_limits, getter) == 42

    async def test_resolver_failure_retains_prior_and_raises(self) -> None:
        sub, app_state = _make_subscriber(
            float_side_effect=RuntimeError("resolver outage"),
        )
        prior = app_state.ws_auth_limits.auth_timeout_seconds
        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed("api", "ws_auth_timeout_seconds")
        assert app_state.ws_auth_limits.auth_timeout_seconds == prior

    async def test_unknown_key_is_noop(self) -> None:
        sub, app_state = _make_subscriber()
        prior = app_state.ws_auth_limits.auth_timeout_seconds
        await sub.on_settings_changed("api", "unrelated")
        assert app_state.ws_auth_limits.auth_timeout_seconds == prior
