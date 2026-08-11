"""Tests for ``RuntimeReloadSettingsSubscriber``.

A watched engine-classifier / external-API / coordination key change triggers a
single ``reload_runtime_services`` rebuild (the values are already re-read from
the live resolver on rebuild). Tests assert the rebuild fires for each watched
namespace and re-raises a rebuild failure, and that a batch of writes costs one
rebuild rather than one each.
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


# The complete watched set, asserted whole below and driven through a reload one
# pair at a time. It has to be complete rather than a sample: a subset cannot
# catch a key added to _WATCHED that no rebuild test exercises, which is how a
# key ends up watched and applied to nothing.
_EXPECTED_WATCHED: tuple[tuple[str, str], ...] = (
    # Both planning-session bounds are baked into a frozen config when the
    # coordinator is assembled, so an edit reaches a session only via a
    # rebuild. Watched as a pair for the same reason they travel as one: a
    # session bounded only in money is unbounded on a flat-rate provider.
    ("budget", "session_token_ceiling"),
    ("coordination", "decomposition_agent_cost_ceiling"),
    ("coordination", "decomposition_model"),
    ("coordination", "enable_coordination_middleware"),
    ("design", "image_generation_enabled"),
    ("design", "image_model"),
    ("engine", "auto_review_on_completion"),
    ("engine", "clarification_enabled"),
    ("engine", "classification_detector_timeout_seconds"),
    ("engine", "classifier_fallback_confidence"),
    ("engine", "classifier_rule_matched_confidence"),
    ("engine", "completion_oracle_enabled"),
    ("engine", "completion_oracle_min_stakes"),
    ("engine", "completion_oracle_reviewer_model"),
    ("engine", "completion_oracle_shadow_mode"),
    # The engine holds its AutoLoopConfig frozen for its lifetime, so an edit
    # to a loop-selection key reaches a task only via a rebuild.
    ("engine", "default_loop_type"),
    ("engine", "enable_agent_middleware"),
    # Each of these three names the connection one runtime collaborator
    # dispatches on, resolved into it while the runtime is assembled, so a
    # reassignment reaches a run only through a rebuild.
    ("engine", "evolution_proposer_model"),
    ("engine", "loop_auto_select_enabled"),
    ("engine", "loop_complexity_overrides"),
    ("engine", "matcher_min_cloud_tier"),
    ("engine", "matcher_min_usable_parameters"),
    ("engine", "matcher_prefer_local"),
    ("engine", "scoping_enabled"),
    ("external_api", "enabled"),
    ("external_api", "provider_type"),
    ("memory", "backend"),
    ("memory", "consolidation_interval"),
    ("memory", "embedder_dims"),
    ("memory", "embedder_model"),
    ("memory", "planning_memory_digest_budget"),
    ("memory", "planning_memory_recall_enabled"),
    ("memory", "procedural_max_tokens"),
    ("memory", "procedural_skill_md_directory"),
    ("memory", "procedural_temperature"),
    ("providers", "gateway_base_url"),
    ("security", "red_team_model"),
    ("security", "vision_verify_model"),
    ("tools", "browser_image_pin"),
    ("tools", "credentialed_mcp_base_url"),
    ("tools", "desktop_driver"),
    ("tools", "desktop_image_pin"),
    ("tools", "desktop_screen_height"),
    ("tools", "desktop_screen_width"),
    # ``build_openhands_loop_deps_or_none`` reads each of these inside the
    # rebuild and then holds the result for the engine's lifetime, so a key
    # missing here is an operator edit that reaches no run.
    ("tools", "openhands_enabled"),
    ("tools", "openhands_idle_timeout_seconds"),
    ("tools", "openhands_image"),
    ("tools", "openhands_max_runtime_seconds"),
    ("tools", "web_search_connection"),
    ("tools", "web_search_enabled"),
    ("tools", "web_search_max_results"),
    ("tools", "web_search_provider"),
)


class TestProtocol:
    def test_isinstance(self) -> None:
        sub, _ = _make_subscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_subscriber_name(self) -> None:
        sub, _ = _make_subscriber()
        assert sub.subscriber_name == "runtime-reload"

    def test_watched_set_is_exactly_the_expected_pairs(self) -> None:
        # Equality, not containment: a subset assertion passes while a newly
        # watched key sits unexercised by the reload test below.
        sub, _ = _make_subscriber()
        assert sub.watched_keys == frozenset(_EXPECTED_WATCHED)

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
    @pytest.mark.parametrize(("namespace", "key"), _EXPECTED_WATCHED)
    async def test_watched_change_triggers_reload(
        self, monkeypatch: pytest.MonkeyPatch, namespace: str, key: str
    ) -> None:
        spy = create_autospec(runtime_builder.reload_runtime_services)
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)
        sub, app_state = _make_subscriber()
        await sub.on_settings_changed([(namespace, key)])
        # The trigger names the key, so a reload line can be attributed to the
        # write that caused it rather than to one of the other watched pairs.
        spy.assert_awaited_once_with(app_state, trigger=f"setting:{namespace}.{key}")

    async def test_reload_failure_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(runtime_builder.reload_runtime_services)
        spy.side_effect = RuntimeError("rebuild boom")
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)
        sub, _ = _make_subscriber()
        with pytest.raises(RuntimeError, match="rebuild boom"):
            await sub.on_settings_changed([("external_api", "enabled")])


class TestBatching:
    """A batch of writes costs one rebuild.

    Saving a settings form writes one key per field. Rebuilding the engine,
    coordinator and pipeline once per field took the org out of service for the
    length of the burst, to converge on the state the last write asked for. The
    batching itself is the dispatcher's (see ``test_dispatcher``); what this
    subscriber owes is to treat the batch it is handed as one trigger.
    """

    async def test_a_batch_costs_one_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(runtime_builder.reload_runtime_services)
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)
        sub, _ = _make_subscriber()

        await sub.on_settings_changed(
            [
                ("engine", "scoping_enabled"),
                ("engine", "clarification_enabled"),
                ("memory", "backend"),
                ("tools", "web_search_enabled"),
            ]
        )

        assert spy.await_count == 1
        # And the one rebuild says what it carried, so a reload line stays
        # attributable to the writes behind it rather than to whichever of
        # them happened to arrive first.
        trigger = spy.await_args.kwargs["trigger"]
        assert trigger.startswith("setting:")
        assert trigger.endswith("+1")
