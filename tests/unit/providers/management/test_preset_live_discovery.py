"""Tests for ``prefer_live_discovery`` preset creation (Ollama Cloud).

A live-discovery gateway preset must NOT seed from the static
``litellm.model_cost`` table (which under ``litellm_provider="openai"``
would surface OpenAI's catalogue), and must run an authenticated live
discovery against the provider endpoint so the full live catalogue is
populated on create.
"""

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from synthorg.api.dto_providers import CreateFromPresetRequest
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.management.service import ProviderManagementService

pytestmark = pytest.mark.unit

_DISCOVER_PATH = "synthorg.providers.management.service.discover_models"
_LITELLM_PATH = "synthorg.providers.management.service.models_from_litellm"


class TestPreferLiveDiscoveryCreate:
    async def test_ollama_cloud_skips_litellm_and_discovers_with_bearer(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Create seeds from the curated list, never ``models_from_litellm``,
        and runs an authenticated discovery that replaces the seed.
        """
        discovered = (
            ProviderModelConfig(id="live-model-001", alias="live-1"),
            ProviderModelConfig(id="live-model-002", alias="live-2"),
        )
        request = CreateFromPresetRequest(
            name="my-ollama",
            preset_name="ollama-cloud",
            api_key=SecretStr("sk-ollama-key"),
        )

        with (
            patch(_DISCOVER_PATH) as discover,
            patch(_LITELLM_PATH) as litellm_models,
        ):
            discover.return_value = discovered
            config = await service.create_from_preset(request)

        # The static litellm catalogue is never consulted for this preset.
        litellm_models.assert_not_called()
        # Live discovery ran once, authenticated with the Bearer key.
        discover.assert_awaited_once()
        assert discover.await_args is not None
        assert discover.await_args.kwargs["headers"] == {
            "Authorization": "Bearer sk-ollama-key",
        }
        # The discovered catalogue replaces the curated seed on create.
        assert {m.id for m in config.models} == {"live-model-001", "live-model-002"}
