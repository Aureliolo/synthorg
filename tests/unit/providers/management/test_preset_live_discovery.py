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
from synthorg.providers.enums import AuthType
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.presets import get_preset

pytestmark = pytest.mark.unit

_DISCOVER_PATH = "synthorg.providers.management.service.discover_models"
_LITELLM_PATH = "synthorg.providers.management._preset_creation.models_from_litellm"


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


class TestLiveDiscoveryGuards:
    async def test_no_api_key_keeps_seed_and_skips_discovery(
        self,
        service: ProviderManagementService,
    ) -> None:
        """A live-discovery preset without a key keeps the seed, never probes."""
        preset = get_preset("ollama-cloud")
        assert preset is not None
        seed = (ProviderModelConfig(id="seed-001", alias="seed"),)

        with patch(_DISCOVER_PATH) as discover:
            result = await service._maybe_discover_preset_models(
                preset,
                preset.default_base_url,
                seed,
                auth_type=AuthType.API_KEY,
                api_key=None,
            )

        discover.assert_not_called()
        assert result == seed

    async def test_bearer_key_not_sent_to_overridden_base_url(
        self,
        service: ProviderManagementService,
    ) -> None:
        """An overridden (non-canonical) base URL never receives the key.

        Confused-deputy guard: the Bearer credential is attached only when
        the base URL still points at the preset's canonical host, so a
        user-overridden endpoint keeps the seed and is never probed with
        the key.
        """
        preset = get_preset("ollama-cloud")
        assert preset is not None
        seed = (ProviderModelConfig(id="seed-001", alias="seed"),)

        with patch(_DISCOVER_PATH) as discover:
            result = await service._maybe_discover_preset_models(
                preset,
                "https://attacker.example.com/v1",
                seed,
                auth_type=AuthType.API_KEY,
                api_key="sk-ollama-key",
            )

        discover.assert_not_called()
        assert result == seed
