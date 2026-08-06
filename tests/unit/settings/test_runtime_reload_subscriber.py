"""Tests for ``RuntimeReloadSettingsSubscriber``.

A watched engine-classifier / external-API / coordination key change triggers a
single ``reload_runtime_services`` rebuild (the values are already re-read from
the live resolver on rebuild). Tests assert the rebuild fires for each watched
namespace, no-ops on an unexpected pair, re-raises a rebuild failure, and that
a burst of writes costs one rebuild rather than one each.
"""

import asyncio
from unittest.mock import create_autospec

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import SettingsSubscriber
from synthorg.settings.subscribers.runtime_reload_subscriber import (
    RuntimeReloadSettingsSubscriber,
)
from synthorg.workers import runtime_builder
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_subscriber(
    window_seconds: float = 0.0,
) -> tuple[RuntimeReloadSettingsSubscriber, AppState]:
    async def _get_float(namespace: str, key: str) -> float:
        assert (namespace, key) == ("engine", "runtime_reload_coalesce_window_seconds")
        return window_seconds

    app_state = make_app_state(
        config=RootConfig(company_name="test"),
        config_resolver=mock_of[ConfigResolver](get_float=_get_float),
    )
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


class _GatedReload:
    """Reload stand-in that blocks until released, recording every call."""

    def __init__(self) -> None:
        self.triggers: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.fail: Exception | None = None

    async def __call__(self, _app_state: AppState, *, trigger: str) -> None:
        self.triggers.append(trigger)
        self.entered.set()
        await self.release.wait()
        if self.fail is not None:
            raise self.fail


class TestCoalescing:
    """A burst of writes costs one rebuild, without weakening what a write means.

    Saving a settings form writes one key per field. Rebuilding the engine,
    coordinator and pipeline once per field took the org out of service for the
    length of the burst, to converge on the state the last write asked for.
    """

    async def test_a_burst_costs_one_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = create_autospec(runtime_builder.reload_runtime_services)
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)
        sub, _ = _make_subscriber()

        await asyncio.gather(
            sub.on_settings_changed("engine", "scoping_enabled"),
            sub.on_settings_changed("engine", "clarification_enabled"),
            sub.on_settings_changed("memory", "backend"),
            sub.on_settings_changed("tools", "web_search_enabled"),
        )

        assert spy.await_count == 1
        # And the one rebuild says what it carried, so a reload line stays
        # attributable to the writes behind it rather than to whichever of
        # them happened to open the batch.
        trigger = spy.await_args.kwargs["trigger"]
        assert trigger.startswith("setting:")
        assert trigger.endswith("+2")

    async def test_every_writer_waits_for_the_rebuild_that_carried_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The property a write depends on: it returns only once the runtime
        # reflects it. Sharing a rebuild must not turn any writer's return
        # into a promise the rebuild has not kept yet.
        gated = _GatedReload()
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", gated)
        sub, _ = _make_subscriber()

        writers = [
            asyncio.create_task(sub.on_settings_changed("engine", "scoping_enabled")),
            asyncio.create_task(sub.on_settings_changed("memory", "backend")),
        ]
        await gated.entered.wait()
        assert not any(task.done() for task in writers)

        gated.release.set()
        await asyncio.gather(*writers)

    async def test_a_failure_reaches_every_writer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gated = _GatedReload()
        gated.fail = RuntimeError("rebuild boom")
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", gated)
        sub, _ = _make_subscriber()

        writers = [
            asyncio.create_task(sub.on_settings_changed("engine", "scoping_enabled")),
            asyncio.create_task(sub.on_settings_changed("memory", "backend")),
        ]
        await gated.entered.wait()
        gated.release.set()
        outcomes = await asyncio.gather(*writers, return_exceptions=True)

        # A writer whose rebuild failed must not be told it succeeded because
        # another writer opened the batch.
        assert [type(outcome) for outcome in outcomes] == [RuntimeError, RuntimeError]

    async def test_a_write_during_a_rebuild_gets_its_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The boundary the batching must not cross: a rebuild already under
        # way read the settings before this write landed, so joining it would
        # return "applied" over a runtime that never saw the value.
        gated = _GatedReload()
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", gated)
        sub, _ = _make_subscriber()

        first = asyncio.create_task(
            sub.on_settings_changed("engine", "scoping_enabled")
        )
        await gated.entered.wait()
        second = asyncio.create_task(sub.on_settings_changed("memory", "backend"))
        gated.release.set()
        await asyncio.gather(first, second)

        assert gated.triggers == [
            "setting:engine.scoping_enabled",
            "setting:memory.backend",
        ]

    async def test_a_cancelled_writer_does_not_cancel_the_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The shared rebuild is not any one writer's to abandon: cancelling
        # the coroutine that opened it would otherwise leave every other
        # writer's value unapplied, and the runtime half-swapped.
        gated = _GatedReload()
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", gated)
        sub, _ = _make_subscriber()

        leaving = asyncio.create_task(
            sub.on_settings_changed("engine", "scoping_enabled")
        )
        staying = asyncio.create_task(sub.on_settings_changed("memory", "backend"))
        await gated.entered.wait()
        leaving.cancel()
        gated.release.set()

        await staying
        assert leaving.cancelled()
        assert gated.triggers == ["setting:engine.scoping_enabled,memory.backend"]

    async def test_the_window_is_read_live_per_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Held rather than re-read, an operator widening the window after a
        # burst of rebuilds would have to restart for the change they made
        # precisely to stop needing one.
        windows: list[float] = [0.0, 0.0]
        spy = create_autospec(runtime_builder.reload_runtime_services)
        monkeypatch.setattr(runtime_builder, "reload_runtime_services", spy)

        async def _get_float(_namespace: str, _key: str) -> float:
            return windows.pop(0)

        app_state = make_app_state(
            config=RootConfig(company_name="test"),
            config_resolver=mock_of[ConfigResolver](get_float=_get_float),
        )
        sub = RuntimeReloadSettingsSubscriber(
            app_state=app_state,
            settings_service=create_autospec(SettingsService, instance=True),
        )

        await sub.on_settings_changed("engine", "scoping_enabled")
        await sub.on_settings_changed("memory", "backend")

        assert windows == []
