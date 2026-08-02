"""Tests for ``RuntimeReloadSettingsSubscriber``.

A watched engine-classifier / external-API / coordination key change triggers a
single ``reload_runtime_services`` rebuild (the values are already re-read from
the live resolver on rebuild). Tests assert the rebuild fires for each watched
namespace, no-ops on an unexpected pair, and re-raises a rebuild failure.
"""

from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.runtime_reload_subscriber import (
    RuntimeReloadSettingsSubscriber,
)
from synthorg.workers import runtime_builder
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_subscriber() -> tuple[RuntimeReloadSettingsSubscriber, AppState]:
    app_state = make_app_state(config=RootConfig(company_name="test"))
    sub = RuntimeReloadSettingsSubscriber(
        app_state=app_state,
        settings_service=create_autospec(SettingsService, instance=True),
    )
    return sub, app_state


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "runtime-reload"

    @pytest.mark.parametrize(
        ("namespace", "key"),
        [
            ("engine", "matcher_min_usable_parameters"),
            ("engine", "matcher_prefer_local"),
            ("engine", "matcher_min_cloud_tier"),
            ("external_api", "enabled"),
            ("coordination", "enable_coordination_middleware"),
            ("coordination", "decomposition_model"),
            ("design", "image_generation_enabled"),
            ("design", "image_model"),
            ("engine", "auto_review_on_completion"),
            ("engine", "completion_oracle_enabled"),
            ("engine", "completion_oracle_shadow_mode"),
            ("engine", "completion_oracle_min_stakes"),
            ("engine", "completion_oracle_reviewer_model_tier"),
            # ``build_openhands_loop_deps_or_none`` reads each of these inside
            # the rebuild and then holds the result for the engine's lifetime,
            # so a key missing here is an operator edit that reaches no run.
            ("tools", "openhands_enabled"),
            ("tools", "openhands_image"),
            ("tools", "openhands_idle_timeout_seconds"),
            ("tools", "openhands_max_runtime_seconds"),
            ("tools", "credentialed_mcp_base_url"),
            ("providers", "gateway_base_url"),
            # The engine holds its AutoLoopConfig frozen for its lifetime, so
            # an edit to a loop-selection key reaches a task only via a rebuild.
            ("engine", "loop_auto_select_enabled"),
            ("engine", "default_loop_type"),
            ("engine", "loop_complexity_overrides"),
            # Both are resolved into the boot AgentEngine when
            # ``build_runtime_services`` (re)builds it, so an edit reaches a
            # run only through a rebuild.
            ("engine", "clarification_enabled"),
            ("engine", "scoping_enabled"),
        ],
    )
    def test_pair_is_watched(self, namespace: str, key: str) -> None:
        sub, _ = _make_subscriber()
        assert (namespace, key) in sub.watched_keys

    def test_ask_policy_keys_are_not_watched(self) -> None:
        # The prompt-only ask-policy keys belong to the ask-policy subscriber,
        # which applies them without a rebuild. Their absence from this
        # subscriber is the assertion, not an omission: watching them here
        # would rebuild the whole runtime to change a prompt section.
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("engine", "ask_policy_enabled") not in watched
        assert ("engine", "ask_policy_extra_directives") not in watched


class TestReload:
    @pytest.mark.parametrize(
        ("namespace", "key"),
        [
            ("engine", "classifier_fallback_confidence"),
            ("external_api", "provider_type"),
            ("coordination", "enable_coordination_middleware"),
            ("coordination", "decomposition_model"),
            ("design", "image_generation_enabled"),
            ("design", "image_model"),
            ("engine", "auto_review_on_completion"),
            ("engine", "completion_oracle_enabled"),
            ("engine", "completion_oracle_shadow_mode"),
            ("engine", "completion_oracle_min_stakes"),
            ("engine", "completion_oracle_reviewer_model_tier"),
            ("tools", "openhands_enabled"),
            ("tools", "credentialed_mcp_base_url"),
            ("providers", "gateway_base_url"),
            ("engine", "loop_auto_select_enabled"),
            ("engine", "default_loop_type"),
            ("engine", "loop_complexity_overrides"),
        ],
    )
    async def test_watched_change_triggers_reload(
        self, monkeypatch: pytest.MonkeyPatch, namespace: str, key: str
    ) -> None:
        spy = create_autospec(runtime_builder.reload_runtime_services)
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)
        sub, app_state = _make_subscriber()
        await sub.on_settings_changed(namespace, key)
        # The trigger names the key, so a reload line can be attributed to the
        # write that caused it rather than to one of the other watched pairs.
        spy.assert_awaited_once_with(app_state, trigger=f"setting:{namespace}.{key}")

    async def test_unknown_key_does_not_reload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(runtime_builder.reload_runtime_services)
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)
        sub, _ = _make_subscriber()
        await sub.on_settings_changed("engine", "unrelated")
        spy.assert_not_awaited()

    async def test_reload_failure_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(runtime_builder.reload_runtime_services)
        spy.side_effect = RuntimeError("rebuild boom")
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)
        sub, _ = _make_subscriber()
        with pytest.raises(RuntimeError, match="rebuild boom"):
            await sub.on_settings_changed("external_api", "enabled")
