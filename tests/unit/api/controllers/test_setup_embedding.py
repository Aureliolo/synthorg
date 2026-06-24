"""Tests for embedding auto-selection during setup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.setup._embedder_setup import (
    auto_select_embedder,
    pick_decomposition_model,
)
from synthorg.memory.embedding.rankings import LMEB_RANKINGS


def _mock_settings_svc() -> AsyncMock:
    svc = AsyncMock()
    svc.save = AsyncMock()
    return svc


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
class TestPickDecompositionModel:
    def test_prefers_large_tier_agent_model(self) -> None:
        agents: list[dict[str, object]] = [
            {"tier": "small", "model": {"model_id": "small-model"}},
            {"tier": "large", "model": {"model_id": "large-model"}},
        ]
        assert pick_decomposition_model(agents) == "large-model"

    def test_falls_back_to_any_agent_with_a_model(self) -> None:
        agents: list[dict[str, object]] = [
            {"tier": "small", "model": {"model_id": "only-model"}},
        ]
        assert pick_decomposition_model(agents) == "only-model"

    def test_returns_none_without_any_model(self) -> None:
        assert pick_decomposition_model([{"tier": "large"}]) is None
        assert pick_decomposition_model([]) is None
