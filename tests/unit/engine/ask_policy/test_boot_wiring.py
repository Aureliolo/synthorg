"""The two paths that actually bind the ambient ask-policy provider.

Boot binds it once; the settings subscriber re-binds it on an edit. Neither is
reachable from a prompt test, so an unbound provider (which renders as "the org
never asks", silently) would otherwise only show up in production.
"""

import pytest

from synthorg.api.lifecycle_helpers.ask_policy_wiring import wire_ask_policy
from synthorg.engine.ask_policy.provider import current_ask_policy_provider
from synthorg.settings.subscribers.ask_policy_subscriber import (
    AskPolicySettingsSubscriber,
)
from tests._shared import make_app_state
from tests.unit.engine.ask_policy.conftest import settings_service

pytestmark = pytest.mark.unit


class TestWireAskPolicy:
    async def test_binds_from_settings(self) -> None:
        await wire_ask_policy(make_app_state(settings_service=settings_service()))

        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is True

    async def test_honours_a_disabled_setting(self) -> None:
        await wire_ask_policy(
            make_app_state(
                settings_service=settings_service(ask_policy_enabled="false")
            )
        )

        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is False

    async def test_no_settings_service_still_binds_the_default(self) -> None:
        # Returning unbound here would be a silent fail-to-OFF: the standing
        # directive would never reach a prompt, with nothing logged as wrong.
        await wire_ask_policy(make_app_state())

        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is True


class TestAskPolicySubscriber:
    @staticmethod
    def _subscriber(**values: str) -> AskPolicySettingsSubscriber:
        settings = settings_service(**values)
        return AskPolicySettingsSubscriber(
            make_app_state(settings_service=settings), settings
        )

    async def test_a_watched_edit_rebinds_the_provider(self) -> None:
        subscriber = self._subscriber(ask_policy_enabled="false")

        await subscriber.on_settings_changed([("engine", "ask_policy_enabled")])

        provider = current_ask_policy_provider()
        assert provider is not None
        assert provider.enabled is False

    async def test_an_unwatched_pair_is_ignored(self) -> None:
        # The dispatcher routes by watched_keys, so reaching here with anything
        # else is a wiring fault: rebinding on it would hide that.
        subscriber = self._subscriber()

        await subscriber.on_settings_changed([("engine", "clarification_enabled")])

        assert current_ask_policy_provider() is None

    @pytest.mark.parametrize(
        "pair",
        [("engine", "ask_policy_enabled"), ("engine", "ask_policy_extra_directives")],
    )
    def test_watches_exactly_the_two_ask_policy_keys(
        self, pair: tuple[str, str]
    ) -> None:
        subscriber = self._subscriber()
        assert pair in subscriber.watched_keys
        assert len(subscriber.watched_keys) == 2

    def test_names_itself_for_the_dispatcher_log(self) -> None:
        assert self._subscriber().subscriber_name == "ask-policy"
