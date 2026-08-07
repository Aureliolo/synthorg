"""Tests for ``A2AClientSettingsSubscriber``.

A change to ``a2a.client_timeout_seconds`` pushes the new per-request timeout
onto the live A2A client. Tests assert the setter is called with the resolved
value, the ``client is None`` path is a silent no-op, resolver failure re-raises
without applying, and an unexpected pair no-ops.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.a2a.client import A2AClient
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.a2a_client_subscriber import (
    A2AClientSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_subscriber(
    *,
    client: A2AClient | None,
    float_return: float = 30.0,
    float_side_effect: BaseException | None = None,
) -> A2AClientSettingsSubscriber:
    resolver = create_autospec(ConfigResolver, instance=True)
    if float_side_effect is not None:
        resolver.get_float.side_effect = float_side_effect
    else:
        resolver.get_float.return_value = float_return
    app_state: AppState = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
        a2a_client=client,
    )
    return A2AClientSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )


class TestProtocol:
    def test_isinstance(self) -> None:
        assert isinstance(
            _make_subscriber(client=mock_of[A2AClient]()), SettingsSubscriber
        )

    def test_watched_keys(self) -> None:
        sub = _make_subscriber(client=mock_of[A2AClient]())
        assert sub.watched_keys == frozenset({("a2a", "client_timeout_seconds")})

    def test_subscriber_name(self) -> None:
        sub = _make_subscriber(client=mock_of[A2AClient]())
        assert sub.subscriber_name == "a2a-client"


class TestApply:
    async def test_timeout_applies_to_client(self) -> None:
        client = mock_of[A2AClient]()
        sub = _make_subscriber(client=client, float_return=12.5)
        await sub.on_settings_changed([("a2a", "client_timeout_seconds")])
        client.set_timeout_seconds.assert_called_once_with(12.5)

    async def test_no_client_is_silent_noop(self) -> None:
        sub = _make_subscriber(client=None)
        await sub.on_settings_changed([("a2a", "client_timeout_seconds")])

    async def test_resolver_failure_reraises(self) -> None:
        client = mock_of[A2AClient]()
        sub = _make_subscriber(
            client=client, float_side_effect=RuntimeError("resolver outage")
        )
        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed([("a2a", "client_timeout_seconds")])
        client.set_timeout_seconds.assert_not_called()

    async def test_unknown_key_is_noop(self) -> None:
        client = mock_of[A2AClient]()
        sub = _make_subscriber(client=client)
        await sub.on_settings_changed([("a2a", "unrelated")])
        client.set_timeout_seconds.assert_not_called()
