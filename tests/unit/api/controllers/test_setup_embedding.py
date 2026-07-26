"""Tests for embedding auto-selection during setup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.setup._embedder_setup import (
    _set_model_if_blank,
    auto_select_embedder,
    pick_decomposition_model_ref,
    pick_model_ref_for_tier,
)
from synthorg.memory.embedding.rankings import LMEB_RANKINGS
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.service import SettingsService


def _bound(provider: str, model_id: str) -> str:
    """Serialize a bound ``{provider, model_id}`` reference for assertions."""
    return serialize_model_ref(ModelRef(provider=provider, model_id=model_id))


def _mock_settings_svc() -> AsyncMock:
    """A SettingsService-spec'd mock (typos raise instead of passing silently)."""
    return AsyncMock(spec=SettingsService)


def _set_many_values(settings_svc: AsyncMock) -> dict[tuple[str, str], str]:
    """Return the ``(namespace, key) -> value`` map from the single set_many call.

    The embedder writes both keys atomically through ``set_many`` (one
    transaction), so the items batch is the first positional argument.
    """
    calls = settings_svc.set_many.call_args_list
    assert len(calls) == 1
    items = calls[0].args[0]
    return {(ns, key): value for ns, key, value in items}


@pytest.mark.unit
class TestAutoSelectEmbedder:
    async def test_selects_best_model(self) -> None:
        top = LMEB_RANKINGS[0]
        settings_svc = _mock_settings_svc()

        await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=(top.model_id,),
        )

        # Should have stored model and dims (not provider).
        values = _set_many_values(settings_svc)
        assert values[("memory", "embedder_model")] == top.model_id
        assert values[("memory", "embedder_dims")] == str(top.output_dims)
        assert ("memory", "embedder_provider") not in values

    async def test_no_models_available_does_not_raise(self) -> None:
        """Auto-selection is best-effort -- no error on failure."""
        settings_svc = _mock_settings_svc()

        await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=(),
        )
        settings_svc.set_many.assert_not_called()

    async def test_no_lmeb_match_does_not_raise(self) -> None:
        settings_svc = _mock_settings_svc()

        await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=("unknown-model-xyz",),
        )
        settings_svc.set_many.assert_not_called()

    async def test_persists_model_and_dims_atomically(self) -> None:
        top = LMEB_RANKINGS[0]
        settings_svc = _mock_settings_svc()

        await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=(top.model_id,),
        )

        # A single atomic batch carries both keys (no partial-write window).
        values = _set_many_values(settings_svc)
        assert ("memory", "embedder_model") in values
        assert ("memory", "embedder_dims") in values

    async def test_respects_operator_chosen_embedder(self) -> None:
        """An already-set embedder is kept; only its dims are (re)resolved."""
        top = LMEB_RANKINGS[0]
        settings_svc = _mock_settings_svc()
        settings_svc.get = AsyncMock(return_value=SimpleNamespace(value=top.model_id))

        # A different model is also available, but the operator's choice wins.
        await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=("some-other-embedder", top.model_id),
        )

        values = _set_many_values(settings_svc)
        # The model is NOT rewritten; only the matching dims are persisted.
        assert ("memory", "embedder_model") not in values
        assert values[("memory", "embedder_dims")] == str(top.output_dims)


@pytest.mark.unit
class TestPickModelRef:
    def test_decomposition_ref_is_bound(self) -> None:
        # A bound ref carries the agent's own provider so the persisted
        # value can never auto-resolve a provider for the id.
        agents: list[dict[str, object]] = [
            {"tier": "small", "model": {"provider": "p1", "model_id": "small-model"}},
            {"tier": "large", "model": {"provider": "p2", "model_id": "large-model"}},
        ]
        assert pick_decomposition_model_ref(agents) == _bound("p2", "large-model")

    def test_tier_ref_is_bound(self) -> None:
        agents: list[dict[str, object]] = [
            {"tier": "small", "model": {"provider": "p1", "model_id": "small-model"}},
        ]
        assert pick_model_ref_for_tier(agents, "small") == _bound("p1", "small-model")

    def test_ref_none_when_provider_blank(self) -> None:
        # A provider-less agent assignment yields no bound ref (never a
        # bare-model write), so the feature stays unset.
        agents: list[dict[str, object]] = [
            {"tier": "large", "model": {"provider": "", "model_id": "m"}},
        ]
        assert pick_decomposition_model_ref(agents) is None
        assert pick_model_ref_for_tier(agents, "large") is None

    def test_ref_none_without_any_model(self) -> None:
        assert pick_decomposition_model_ref([]) is None
        assert pick_model_ref_for_tier([{"tier": "small"}], "small") is None

    def test_half_bound_agent_does_not_end_the_scan(self) -> None:
        """A provider with no model id yields no ref, so it must not win.

        Stopping there would report "no bound model" while a fully bound
        agent sits later in the roster.
        """
        agents: list[dict[str, object]] = [
            {"tier": "small", "model": {"provider": "p1", "model_id": ""}},
            {"tier": "small", "model": {"provider": "p2", "model_id": "real-model"}},
        ]

        assert pick_model_ref_for_tier(agents, "small") == _bound("p2", "real-model")
        assert pick_decomposition_model_ref(agents) == _bound("p2", "real-model")

    def test_model_id_without_a_provider_does_not_end_the_scan(self) -> None:
        agents: list[dict[str, object]] = [
            {"tier": "large", "model": {"provider": "", "model_id": "orphan"}},
            {"tier": "large", "model": {"provider": "p3", "model_id": "bound"}},
        ]

        assert pick_model_ref_for_tier(agents, "large") == _bound("p3", "bound")


@pytest.mark.unit
class TestSetModelIfBlank:
    async def test_sets_when_blank(self) -> None:
        svc = _mock_settings_svc()
        svc.get.return_value = SimpleNamespace(value="")
        ref = _bound("example-provider", "example-medium-001")
        await _set_model_if_blank(svc, "research", "model", ref)
        svc.set.assert_awaited_once_with("research", "model", ref)

    async def test_skips_when_already_set(self) -> None:
        svc = _mock_settings_svc()
        svc.get.return_value = SimpleNamespace(value="operator-choice")
        await _set_model_if_blank(svc, "research", "model", _bound("p", "m"))
        svc.set.assert_not_awaited()

    async def test_skips_when_no_ref(self) -> None:
        svc = _mock_settings_svc()
        await _set_model_if_blank(svc, "research", "model", None)
        svc.get.assert_not_awaited()
        svc.set.assert_not_awaited()
