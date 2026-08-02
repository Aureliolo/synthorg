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

    def test_watched_keys_span_engine_external_api_coordination(self) -> None:
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("engine", "matcher_min_usable_parameters") in watched
        assert ("engine", "matcher_prefer_local") in watched
        assert ("engine", "matcher_min_cloud_tier") in watched
        assert ("external_api", "enabled") in watched
        assert ("coordination", "enable_coordination_middleware") in watched
        assert ("coordination", "decomposition_model") in watched
        assert ("design", "image_generation_enabled") in watched
        assert ("design", "image_model") in watched

    def test_watched_keys_keep_the_two_tool_gates(self) -> None:
        # The tool gates change the agent toolset, which only a runtime rebuild
        # can install, so they stay here rather than migrating to the
        # ask-policy subscriber (which owns the prompt-only keys).
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("engine", "clarification_enabled") in watched
        assert ("engine", "scoping_enabled") in watched
        assert ("engine", "ask_policy_enabled") not in watched
        assert ("engine", "ask_policy_extra_directives") not in watched

    def test_watched_keys_include_auto_review_and_completion_oracle(self) -> None:
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("engine", "auto_review_on_completion") in watched
        assert ("engine", "completion_oracle_enabled") in watched
        assert ("engine", "completion_oracle_shadow_mode") in watched
        assert ("engine", "completion_oracle_min_stakes") in watched
        assert ("engine", "completion_oracle_reviewer_model_tier") in watched

    def test_watched_keys_cover_every_openhands_deps_input(self) -> None:
        # ``build_openhands_loop_deps_or_none`` reads each of these inside the
        # rebuild and then holds the result for the engine's lifetime, so a key
        # missing here is an operator edit that reaches no run.
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("tools", "openhands_enabled") in watched
        assert ("tools", "openhands_image") in watched
        assert ("tools", "openhands_idle_timeout_seconds") in watched
        assert ("tools", "openhands_max_runtime_seconds") in watched
        assert ("tools", "credentialed_mcp_base_url") in watched
        assert ("providers", "gateway_base_url") in watched

    def test_watched_keys_include_loop_selection(self) -> None:
        # The engine holds its AutoLoopConfig frozen for its lifetime, so an
        # edit to any of these reaches a task only through a rebuild.
        sub, _ = _make_subscriber()
        watched = sub.watched_keys
        assert ("engine", "loop_auto_select_enabled") in watched
        assert ("engine", "default_loop_type") in watched
        assert ("engine", "loop_complexity_overrides") in watched


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
        spy.assert_awaited_once_with(app_state)

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
