"""Tests for ``EscalationReconnectSettingsSubscriber``.

A change to ``communication.escalation_subscriber_reconnect_delay_seconds``
pushes the new delay onto the live escalation-notify subscriber, which reads it
per reconnect attempt. Tests assert the setter is called with the resolved
value, the absent-subscriber path is a silent no-op, resolver failure re-raises
without applying, and an unexpected pair no-ops.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.communication.conflict_resolution.escalation.notify import (
    EscalationNotifySubscriber,
)
from synthorg.communication.state import CommunicationStateSlice
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.escalation_reconnect_subscriber import (
    EscalationReconnectSettingsSubscriber,
)
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_KEY = "escalation_subscriber_reconnect_delay_seconds"


def _make_subscriber(
    *,
    notify_subscriber: EscalationNotifySubscriber | None,
    float_return: float = 2.5,
    float_side_effect: BaseException | None = None,
) -> EscalationReconnectSettingsSubscriber:
    resolver = create_autospec(ConfigResolver, instance=True)
    if float_side_effect is not None:
        resolver.get_float.side_effect = float_side_effect
    else:
        resolver.get_float.return_value = float_return
    app_state: AppState = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=resolver,
    )
    if notify_subscriber is not None:
        app_state.wire(
            CommunicationStateSlice,
            escalation_notify_subscriber=notify_subscriber,
        )
    return EscalationReconnectSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )


class TestProtocol:
    def test_isinstance(self) -> None:
        sub = _make_subscriber(notify_subscriber=mock_of[EscalationNotifySubscriber]())
        assert isinstance(sub, SettingsSubscriber)

    def test_watched_keys(self) -> None:
        sub = _make_subscriber(notify_subscriber=mock_of[EscalationNotifySubscriber]())
        assert sub.watched_keys == frozenset({("communication", _KEY)})

    def test_subscriber_name(self) -> None:
        sub = _make_subscriber(notify_subscriber=mock_of[EscalationNotifySubscriber]())
        assert sub.subscriber_name == "escalation-reconnect"


class TestApply:
    async def test_delay_applies_to_subscriber(self) -> None:
        notify = mock_of[EscalationNotifySubscriber]()
        sub = _make_subscriber(notify_subscriber=notify, float_return=4.0)
        await sub.on_settings_changed([("communication", _KEY)])
        notify.set_reconnect_delay_seconds.assert_called_once_with(4.0)

    async def test_absent_subscriber_is_silent_noop(self) -> None:
        sub = _make_subscriber(notify_subscriber=None)
        await sub.on_settings_changed([("communication", _KEY)])

    async def test_resolver_failure_reraises(self) -> None:
        notify = mock_of[EscalationNotifySubscriber]()
        sub = _make_subscriber(
            notify_subscriber=notify,
            float_side_effect=RuntimeError("resolver outage"),
        )
        with pytest.raises(RuntimeError, match="resolver outage"):
            await sub.on_settings_changed([("communication", _KEY)])
        notify.set_reconnect_delay_seconds.assert_not_called()

    async def test_unknown_key_is_noop(self) -> None:
        notify = mock_of[EscalationNotifySubscriber]()
        sub = _make_subscriber(notify_subscriber=notify)
        await sub.on_settings_changed([("communication", "unrelated")])
        notify.set_reconnect_delay_seconds.assert_not_called()
