"""Tests for ProviderManagementService (CRUD, connections, validation)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from synthorg.api.dto_providers import (
    CreateFromPresetRequest,
    CreateProviderRequest,
    UpdateProviderRequest,
)
from synthorg.api.dto_providers import TestConnectionRequest as ConnTestRequest
from synthorg.api.state import AppState
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import (
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
    ProviderValidationError,
)
from synthorg.providers.management._credential_helpers import resolve_provider_api_key
from synthorg.providers.management.service import ProviderManagementService
from synthorg.providers.state import ProvidersStateSlice, provider_registry_of
from synthorg.settings.service import SettingsService

from .conftest import make_create_request


@pytest.mark.unit
class TestCreateProvider:
    async def test_create_provider_success(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = make_create_request()
        result = await service.create_provider(request)
        assert result.driver == "litellm"
        assert result.auth_type == AuthType.NONE

    async def test_create_provider_duplicate_name_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)

        with pytest.raises(ProviderAlreadyExistsError, match="already exists"):
            await service.create_provider(request)

    async def test_create_provider_persists_to_settings(
        self,
        service: ProviderManagementService,
        settings_service: SettingsService,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)

        result = await settings_service.get("providers", "configs")
        data = json.loads(result.value)
        assert data["schema_version"] == 1
        assert "test-provider" in data["providers"]

    async def test_create_provider_rebuilds_registry(
        self,
        service: ProviderManagementService,
        app_state: AppState,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)
        assert app_state.slice(ProvidersStateSlice).registry is not None
        assert "test-provider" in provider_registry_of(app_state)

    async def test_create_provider_swaps_app_state(
        self,
        service: ProviderManagementService,
        app_state: AppState,
    ) -> None:
        request = make_create_request()
        await service.create_provider(request)
        assert app_state.slice(ProvidersStateSlice).model_router is not None


@pytest.mark.unit
class TestUpdateProvider:
    async def test_update_provider_success(
        self,
        service: ProviderManagementService,
    ) -> None:
        await service.create_provider(make_create_request())
        update = UpdateProviderRequest(
            base_url="http://localhost:9999",
        )
        result = await service.update_provider("test-provider", update)
        assert result.base_url == "http://localhost:9999"

    async def test_update_provider_nonexistent_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        update = UpdateProviderRequest(driver="litellm")
        with pytest.raises(ProviderNotFoundError, match="not found"):
            await service.update_provider("nonexistent", update)

    async def test_update_provider_partial_fields(
        self,
        service: ProviderManagementService,
    ) -> None:
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.API_KEY,
                api_key="sk-original",
            ),
        )
        update = UpdateProviderRequest(
            base_url="http://localhost:5000",
        )
        result = await service.update_provider("test-provider", update)
        assert result.base_url == "http://localhost:5000"
        # The api_key is minted into the catalog on create and referenced by
        # connection_name; a partial update preserves that reference.
        assert result.connection_name == "provider-test-provider"


@pytest.mark.unit
class TestDeleteProvider:
    async def test_delete_provider_success(
        self,
        service: ProviderManagementService,
    ) -> None:
        await service.create_provider(make_create_request())
        await service.delete_provider("test-provider")

        providers = await service.list_providers()
        assert "test-provider" not in providers

    async def test_delete_provider_nonexistent_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        with pytest.raises(ProviderNotFoundError, match="not found"):
            await service.delete_provider("nonexistent")


@pytest.mark.unit
class TestTestConnection:
    async def test_test_connection_no_models_error(
        self,
        service: ProviderManagementService,
    ) -> None:
        await service.create_provider(
            CreateProviderRequest(
                name="empty-provider",
                driver="litellm",
                auth_type=AuthType.NONE,
                models=(),
            ),
        )
        request = ConnTestRequest()
        result = await service.test_connection("empty-provider", request)
        assert result.success is False
        assert "no models" in (result.error or "").lower()

    async def test_test_connection_provider_not_found(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = ConnTestRequest()
        with pytest.raises(ProviderNotFoundError, match="not found"):
            await service.test_connection("nonexistent", request)

    async def test_test_connection_success(
        self,
        service: ProviderManagementService,
    ) -> None:
        await service.create_provider(make_create_request())

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "pong"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1
        mock_response.id = "test-id"

        with patch(
            "synthorg.providers.drivers.litellm_driver._litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            request = ConnTestRequest()
            result = await service.test_connection(
                "test-provider",
                request,
            )
            assert result.success is True
            assert result.latency_ms is not None
            assert result.model_tested == "test-model-001"

    async def test_test_connection_auth_failure(
        self,
        service: ProviderManagementService,
    ) -> None:
        await service.create_provider(make_create_request())

        from synthorg.providers.errors import AuthenticationError

        with patch(
            "synthorg.providers.drivers.litellm_driver._litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=AuthenticationError("Invalid key"),
        ):
            request = ConnTestRequest()
            result = await service.test_connection(
                "test-provider",
                request,
            )
            assert result.success is False
            assert result.error is not None


@pytest.mark.unit
class TestCreateFromPreset:
    async def test_create_from_preset(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = CreateFromPresetRequest(
            preset_name="ollama",
            name="my-ollama",
        )
        result = await service.create_from_preset(request)
        assert result.auth_type == AuthType.NONE
        assert result.base_url == "http://localhost:11434"

    async def test_create_from_preset_with_overrides(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = CreateFromPresetRequest(
            preset_name="ollama",
            name="my-ollama",
            base_url="http://gpu-server:11434",
        )
        result = await service.create_from_preset(request)
        assert result.base_url == "http://gpu-server:11434"

    async def test_create_from_preset_unknown_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        request = CreateFromPresetRequest(
            preset_name="nonexistent",
            name="my-provider",
        )
        with pytest.raises(ProviderValidationError, match="Unknown preset"):
            await service.create_from_preset(request)

    async def test_create_from_preset_requires_base_url(
        self,
        service: ProviderManagementService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Presets with requires_base_url=True reject creation without a URL."""
        from synthorg.providers.presets import CloudPreset

        test_preset = CloudPreset(
            name="test-provider",
            display_name="Test Provider",
            description="Test preset requiring base URL",
            driver="litellm",
            litellm_provider="test-provider",
            auth_type=AuthType.API_KEY,
            supported_auth_types=(AuthType.API_KEY,),
            requires_base_url=True,
            default_models=(),
        )
        monkeypatch.setattr(
            "synthorg.providers.management._preset_creation.get_preset",
            lambda name: test_preset if name == "test-provider" else None,
        )
        request = CreateFromPresetRequest(
            preset_name="test-provider",
            name="my-provider",
            api_key=SecretStr("test-key"),
        )
        with pytest.raises(ProviderValidationError, match="requires a base URL"):
            await service.create_from_preset(request)


@pytest.mark.unit
class TestConcurrency:
    async def test_concurrent_creates_serialized(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Verify the lock prevents race conditions."""
        import asyncio

        requests = [
            CreateProviderRequest(
                name=f"provider-{i:02d}",
                driver="litellm",
                auth_type=AuthType.NONE,
                models=(
                    ProviderModelConfig(
                        id=f"model-{i:02d}",
                        alias=f"alias-{i:02d}",
                    ),
                ),
            )
            for i in range(5)
        ]
        results = await asyncio.gather(
            *(service.create_provider(r) for r in requests),
        )
        assert len(results) == 5
        providers = await service.list_providers()
        assert len(providers) == 5


@pytest.mark.unit
class TestClearApiKey:
    async def test_clear_api_key_on_api_key_auth_rejected(
        self,
        service: ProviderManagementService,
    ) -> None:
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.API_KEY,
                api_key="sk-original",
            ),
        )
        # Catalog-only credentials: API_KEY auth has no other credential
        # source, so clearing it would leave the provider unable to
        # authenticate. The operator must switch auth_type or delete.
        update = UpdateProviderRequest(clear_api_key=True)
        with pytest.raises(ProviderValidationError, match="switch auth_type"):
            await service.update_provider("test-provider", update)

    async def test_api_key_takes_precedence_over_clear(
        self,
        service: ProviderManagementService,
    ) -> None:
        """api_key + clear_api_key is rejected by the DTO validator."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="mutually exclusive"):
            UpdateProviderRequest(api_key=SecretStr("new-key"), clear_api_key=True)


@pytest.mark.unit
class TestProviderNameValidation:
    @pytest.mark.parametrize(
        "name",
        ["a", "-bad", "bad-", "My-Provider", "presets", "from-preset"],
    )
    def test_invalid_names_rejected(self, name: str) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CreateProviderRequest(
                name=name,
                driver="litellm",
                auth_type=AuthType.NONE,
            )

    @pytest.mark.parametrize(
        "name",
        ["ab", "my-provider", "test-01", "ollama-local"],
    )
    def test_valid_names_accepted(self, name: str) -> None:
        request = CreateProviderRequest(
            name=name,
            driver="litellm",
            auth_type=AuthType.NONE,
        )
        assert request.name == name


@pytest.mark.unit
class TestAuthTypeTransitions:
    """Tests for auth-type transition credential cleanup."""

    async def test_switch_to_api_key_clears_oauth_fields(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Switching from oauth to api_key clears all OAuth fields."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.OAUTH,
                api_key="oauth-token",
                oauth_token_url="https://auth.example.com/token",
                oauth_client_id="client-123",
                oauth_client_secret="secret-456",
            ),
        )
        update = UpdateProviderRequest(
            auth_type=AuthType.API_KEY,
        )
        result = await service.update_provider("test-provider", update)
        assert result.auth_type == AuthType.API_KEY
        assert result.oauth_token_url is None
        assert result.oauth_client_id is None
        assert result.oauth_client_secret is None
        assert result.oauth_scope is None

    async def test_switch_to_none_clears_all_credentials(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Switching to none auth clears API key and all credential fields."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.API_KEY,
                api_key="sk-test-key",
            ),
        )
        update = UpdateProviderRequest(
            auth_type=AuthType.NONE,
        )
        result = await service.update_provider("test-provider", update)
        assert result.auth_type == AuthType.NONE
        # NONE auth has no api-key connection; the backing connection is
        # deleted and connection_name cleared.
        assert result.connection_name is None

    async def test_switch_to_custom_header_clears_oauth_fields(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Switching from oauth to custom_header clears all OAuth fields."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.OAUTH,
                api_key="oauth-token",
                oauth_token_url="https://auth.example.com/token",
                oauth_client_id="client-123",
                oauth_client_secret="secret-456",
            ),
        )
        update = UpdateProviderRequest(
            auth_type=AuthType.CUSTOM_HEADER,
            custom_header_name="X-Auth",
            custom_header_value=SecretStr("header-val"),
        )
        result = await service.update_provider("test-provider", update)
        assert result.auth_type == AuthType.CUSTOM_HEADER
        assert result.oauth_token_url is None
        assert result.oauth_client_id is None
        assert result.oauth_client_secret is None
        assert result.oauth_scope is None

    async def test_explicit_credential_overrides_clear(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Explicit credentials win over auth-type-transition clearing."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.OAUTH,
                api_key="old-token",
                oauth_token_url="https://auth.example.com/token",
                oauth_client_id="client-123",
                oauth_client_secret="secret-456",
            ),
        )
        # Switch to api_key but also provide an explicit key
        update = UpdateProviderRequest(
            auth_type=AuthType.API_KEY,
            api_key=SecretStr("sk-new-explicit-key"),
        )
        result = await service.update_provider("test-provider", update)
        assert result.auth_type == AuthType.API_KEY
        # The explicit key is minted into the catalog and resolved via
        # connection_name.
        assert (
            await resolve_provider_api_key(service._app_state, result)
            == "sk-new-explicit-key"
        )
        # OAuth fields should still be cleared
        assert result.oauth_token_url is None
        assert result.oauth_client_id is None
        assert result.oauth_client_secret is None

    async def test_switch_from_subscription_to_api_key_clears_subscription(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Switching from subscription to api_key clears token and tos."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.SUBSCRIPTION,
                subscription_token="test-subscription-token",
                tos_accepted=True,
            ),
        )
        update = UpdateProviderRequest(
            auth_type=AuthType.API_KEY,
            api_key=SecretStr("sk-new-key"),
        )
        result = await service.update_provider("test-provider", update)
        assert result.auth_type == AuthType.API_KEY
        assert (
            await resolve_provider_api_key(service._app_state, result) == "sk-new-key"
        )
        assert result.subscription_token is None
        assert result.tos_accepted_at is None

    async def test_switch_to_subscription_sets_token(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Switching to subscription sets the subscription_token."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.API_KEY,
                api_key="sk-old",
            ),
        )
        update = UpdateProviderRequest(
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token=SecretStr("test-subscription-token"),
            tos_accepted=True,
        )
        result = await service.update_provider("test-provider", update)
        assert result.auth_type == AuthType.SUBSCRIPTION
        assert result.subscription_token == "test-subscription-token"
        # The api-key connection is deleted on switch away from API_KEY.
        assert result.connection_name is None
        assert result.tos_accepted_at is not None

    async def test_subscription_tos_accepted_stamps_timestamp(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Setting tos_accepted=True re-stamps tos_accepted_at."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.SUBSCRIPTION,
                subscription_token="test-subscription-token",
                tos_accepted=True,
            ),
        )
        # Re-accept ToS to re-stamp
        update = UpdateProviderRequest(tos_accepted=True)
        result = await service.update_provider("test-provider", update)
        assert result.tos_accepted_at is not None

    async def test_clear_subscription_token_raises_validation(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Clearing subscription_token while staying on SUBSCRIPTION fails validation.

        The ProviderConfig validator requires subscription_token when
        auth_type is SUBSCRIPTION, so clearing it without changing
        auth type raises a ValidationError.
        """
        from pydantic import ValidationError

        await service.create_provider(
            make_create_request(
                auth_type=AuthType.SUBSCRIPTION,
                subscription_token="test-subscription-token",
                tos_accepted=True,
            ),
        )
        update = UpdateProviderRequest(clear_subscription_token=True)
        with pytest.raises(ValidationError, match="subscription_token"):
            await service.update_provider("test-provider", update)


@pytest.mark.unit
class TestValidateAndPersistFailure:
    """Tests for _validate_and_persist error paths."""

    async def test_create_provider_raises_on_registry_build_failure(
        self,
        service: ProviderManagementService,
    ) -> None:
        """ProviderRegistry.from_config failure wraps into ProviderValidationError."""
        request = make_create_request()
        with (
            patch(
                "synthorg.providers.management._persistence.ProviderRegistry.from_config",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(
                ProviderValidationError,
                match="validation failed",
            ),
        ):
            await service.create_provider(request)


@pytest.mark.unit
class TestTestConnectionExtended:
    """Extended connection test coverage."""

    async def test_test_connection_explicit_model(
        self,
        service: ProviderManagementService,
    ) -> None:
        """test_connection uses request.model when provided."""
        await service.create_provider(
            CreateProviderRequest(
                name="test-provider",
                driver="litellm",
                auth_type=AuthType.NONE,
                models=(
                    ProviderModelConfig(id="model-a", alias="primary"),
                    ProviderModelConfig(id="model-b", alias="secondary"),
                ),
            ),
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "pong"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1
        mock_response.id = "test-id"

        with patch(
            "synthorg.providers.drivers.litellm_driver._litellm.acompletion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            request = ConnTestRequest(model="model-b")
            result = await service.test_connection("test-provider", request)
            assert result.success is True
            assert result.model_tested == "model-b"

    async def test_do_test_connection_generic_exception(
        self,
        service: ProviderManagementService,
    ) -> None:
        """Generic exceptions are caught and include the type name in error."""
        await service.create_provider(make_create_request())

        with patch(
            "synthorg.providers.drivers.litellm_driver.LiteLLMDriver.__init__",
            side_effect=RuntimeError("driver init failed"),
        ):
            request = ConnTestRequest()
            result = await service.test_connection("test-provider", request)
            assert result.success is False
            assert result.error is not None
            assert "RuntimeError" in result.error


@pytest.mark.unit
class TestSerializeRoundTrip:
    """Tests for provider serialization round-trip fidelity."""

    async def test_serialize_round_trip(
        self,
        service: ProviderManagementService,
        settings_service: SettingsService,
    ) -> None:
        """Providers survive a serialize -> deserialize cycle."""
        await service.create_provider(
            make_create_request(
                auth_type=AuthType.API_KEY,
                api_key="sk-round-trip",
                base_url="http://localhost:8080",
            ),
        )
        # Read the serialized blob from settings
        setting = await settings_service.get("providers", "configs")
        # The plaintext key is catalog-only and must never reach the persisted
        # provider-config blob (a serializer regression that embedded it would
        # otherwise pass every other assertion below).
        assert "sk-round-trip" not in setting.value
        serialized = json.loads(setting.value)
        assert serialized["schema_version"] == 1
        assert "test-provider" in serialized["providers"]

        # Deserialize back to ProviderConfig
        raw = serialized["providers"]["test-provider"]
        restored = ProviderConfig.model_validate(raw)
        assert restored.driver == "litellm"
        assert restored.auth_type == AuthType.API_KEY
        # The secret is stored in the catalog, never serialised on the config;
        # the persisted blob carries only the connection_name reference.
        assert restored.connection_name == "provider-test-provider"
        assert restored.base_url == "http://localhost:8080"
