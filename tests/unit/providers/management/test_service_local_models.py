"""Tests for local model management (pull, delete, config)."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from synthorg.api.dto_providers import CreateFromPresetRequest
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.management.service import ProviderManagementService

from .conftest import make_create_request

pytestmark = pytest.mark.unit


class TestLocalModelManagement:
    """Tests for pull_model, delete_model, update_model_config."""

    async def _create_ollama_provider(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Helper: create an Ollama provider via from-preset."""
        with (
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(ProviderModelConfig(id="test-model:latest"),),
            ),
        ):
            request = CreateFromPresetRequest(
                preset_name="ollama",
                name="my-ollama",
            )
            await service.create_from_preset(request)

    async def test_pull_model_delegates_to_manager(
        self,
        service: ProviderManagementService,
    ) -> None:
        """pull_model yields events from the local model manager."""
        from synthorg.providers.management.local_models import PullProgressEvent

        await self._create_ollama_provider(service)

        fake_events = [
            PullProgressEvent(status="downloading", progress_percent=50.0),
            PullProgressEvent(status="success", done=True),
        ]

        captured_model_names: list[str] = []

        async def fake_pull(model_name: str) -> AsyncIterator[PullProgressEvent]:
            captured_model_names.append(model_name)
            for evt in fake_events:
                yield evt

        with patch(
            "synthorg.providers.management.local_models.get_local_model_manager",
        ) as mock_factory:
            mock_manager = AsyncMock()
            mock_manager.pull_model = fake_pull
            mock_factory.return_value = mock_manager

            events = [
                evt async for evt in service.pull_model("my-ollama", "test-model:7b")
            ]

        assert len(events) == 2
        assert events[1].done is True
        assert captured_model_names == ["test-model:7b"]

    async def test_pull_model_unsupported_preset_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Providers without pull support raise ProviderValidationError."""
        # Create a provider with no preset_name (manual creation)
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.API_KEY,
                api_key="sk-test",
            ),
        )
        with pytest.raises(ProviderValidationError, match="does not support"):
            async for _ in service.pull_model("test-provider", "some-model"):
                pass

    async def test_delete_model_delegates_and_refreshes(
        self,
        service: ProviderManagementService,
    ) -> None:
        """delete_model calls manager.delete_model then refreshes."""
        await self._create_ollama_provider(service)

        with (
            patch(
                "synthorg.providers.management.local_models.get_local_model_manager",
            ) as mock_factory,
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(ProviderModelConfig(id="remaining-model"),),
            ) as mock_discover,
        ):
            mock_manager = AsyncMock()
            mock_factory.return_value = mock_manager

            await service.delete_model("my-ollama", "test-model:latest")

        mock_manager.delete_model.assert_awaited_once_with("test-model:latest")
        mock_discover.assert_awaited()

    async def test_update_model_config_persists(
        self,
        service: ProviderManagementService,
    ) -> None:
        """update_model_config updates local_params on the model."""
        from synthorg.config.schema import LocalModelParams

        await self._create_ollama_provider(service)

        params = LocalModelParams(num_ctx=4096, num_gpu_layers=32)
        result = await service.update_model_config(
            "my-ollama",
            "test-model:latest",
            params,
        )

        model = next(m for m in result.models if m.id == "test-model:latest")
        assert model.local_params is not None
        assert model.local_params.num_ctx == 4096
        assert model.local_params.num_gpu_layers == 32

    async def test_update_model_config_unknown_model_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Updating config for a nonexistent model raises."""
        from synthorg.config.schema import LocalModelParams
        from synthorg.providers.errors import ProviderModelNotFoundError

        await self._create_ollama_provider(service)

        with pytest.raises(ProviderModelNotFoundError, match="not found"):
            await service.update_model_config(
                "my-ollama",
                "nonexistent-model",
                LocalModelParams(num_ctx=4096),
            )


class TestCreateFromPresetLocalSkipsLitellm:
    """Bug fix: local presets (auth_type=NONE) must skip models_from_litellm.

    The static LiteLLM database returns stale/wrong models for local
    providers.  Ollama gets stale Ollama entries; LM Studio and vLLM
    get OpenAI cloud models (their litellm_provider is "openai").
    """

    @pytest.mark.parametrize(
        "preset_name",
        [
            pytest.param("ollama", id="ollama"),
            pytest.param("lm-studio", id="lm-studio"),
            pytest.param("vllm", id="vllm"),
        ],
    )
    async def test_local_preset_does_not_call_models_from_litellm(
        self,
        service: ProviderManagementService,
        preset_name: str,
    ) -> None:
        """Local presets skip models_from_litellm entirely."""
        with (
            patch(
                "synthorg.providers.management.service.models_from_litellm",
            ) as mock_litellm,
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
                return_value=(ProviderModelConfig(id="local-model"),),
            ),
        ):
            request = CreateFromPresetRequest(
                preset_name=preset_name,
                name=f"my-{preset_name}",
            )
            result = await service.create_from_preset(request)

        mock_litellm.assert_not_called()
        assert any(m.id == "local-model" for m in result.models)

    async def test_cloud_preset_still_calls_models_from_litellm(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Cloud presets (auth_type != NONE) still use models_from_litellm."""
        litellm_models = (
            ProviderModelConfig(id="cloud-model-a"),
            ProviderModelConfig(id="cloud-model-b"),
        )
        with patch(
            "synthorg.providers.management.service.models_from_litellm",
            return_value=litellm_models,
        ) as mock_litellm:
            request = CreateFromPresetRequest(
                preset_name="openrouter",
                name="my-openrouter",
                api_key=SecretStr("sk-test-key"),
            )
            result = await service.create_from_preset(request)

        mock_litellm.assert_called_once_with("openrouter")
        assert result.models == litellm_models

    async def test_local_preset_with_explicit_models_skips_both(
        self,
        service: ProviderManagementService,
    ) -> None:
        """User-provided models bypass both litellm and discovery."""
        user_models = (ProviderModelConfig(id="user-model"),)
        with (
            patch(
                "synthorg.providers.management.service.models_from_litellm",
            ) as mock_litellm,
            patch(
                "synthorg.providers.management.service.discover_models",
                new_callable=AsyncMock,
            ) as mock_discover,
        ):
            request = CreateFromPresetRequest(
                preset_name="ollama",
                name="my-ollama",
                models=user_models,
            )
            result = await service.create_from_preset(request)

        mock_litellm.assert_not_called()
        mock_discover.assert_not_awaited()
        assert result.models == user_models
