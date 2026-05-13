"""Tests for embedding auto-selection during setup."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.setup.agent_helpers import auto_select_embedder
from synthorg.memory.embedding.rankings import LMEB_RANKINGS


def _mock_settings_svc() -> AsyncMock:
    svc = AsyncMock()
    svc.set = AsyncMock()
    return svc


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
        calls = settings_svc.set.call_args_list
        values = {(c.args[0], c.args[1]): c.args[2] for c in calls}
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
        settings_svc.set.assert_not_called()

    async def test_no_lmeb_match_does_not_raise(self) -> None:
        settings_svc = _mock_settings_svc()

        await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=("unknown-model-xyz",),
        )
        settings_svc.set.assert_not_called()

    async def test_persists_model_and_dims(self) -> None:
        top = LMEB_RANKINGS[0]
        settings_svc = _mock_settings_svc()

        await auto_select_embedder(
            settings_svc=settings_svc,
            available_model_ids=(top.model_id,),
        )

        calls = settings_svc.set.call_args_list
        keys_set = {(c.args[0], c.args[1]) for c in calls}
        assert ("memory", "embedder_model") in keys_set
        assert ("memory", "embedder_dims") in keys_set
