"""Tests for ProviderConfig auth type validation."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.config.schema import ProviderConfig
from synthorg.providers.enums import AuthType

_TOS_ACCEPTED = datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
class TestAuthTypeEnum:
    def test_auth_type_enum_values(self) -> None:
        assert AuthType.API_KEY.value == "api_key"
        assert AuthType.OAUTH.value == "oauth"
        assert AuthType.CUSTOM_HEADER.value == "custom_header"
        assert AuthType.SUBSCRIPTION.value == "subscription"
        assert AuthType.NONE.value == "none"
        assert len(AuthType) == 5


@pytest.mark.unit
class TestProviderConfigAuth:
    def test_backward_compat_defaults_to_api_key(self) -> None:
        config = ProviderConfig(driver="litellm", connection_name="provider-test")
        assert config.auth_type == AuthType.API_KEY

    def test_oauth_valid(self) -> None:
        config = ProviderConfig(
            driver="litellm",
            auth_type=AuthType.OAUTH,
            oauth_token_url="https://auth.example.com/token",
            oauth_client_id="client-123",
            oauth_client_secret="secret-456",
        )
        assert config.auth_type == AuthType.OAUTH
        assert config.oauth_token_url == "https://auth.example.com/token"

    def test_oauth_missing_token_url_raises(self) -> None:
        with pytest.raises(ValidationError, match="oauth_token_url"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.OAUTH,
                oauth_client_id="client-123",
                oauth_client_secret="secret-456",
            )

    def test_oauth_missing_client_id_raises(self) -> None:
        with pytest.raises(ValidationError, match="oauth_client_id"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.OAUTH,
                oauth_token_url="https://auth.example.com/token",
                oauth_client_secret="secret-456",
            )

    def test_oauth_missing_client_secret_raises(self) -> None:
        with pytest.raises(ValidationError, match="oauth_client_secret"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.OAUTH,
                oauth_token_url="https://auth.example.com/token",
                oauth_client_id="client-123",
            )

    def test_custom_header_valid(self) -> None:
        config = ProviderConfig(
            driver="litellm",
            auth_type=AuthType.CUSTOM_HEADER,
            custom_header_name="X-Custom-Auth",
            custom_header_value="token-789",
        )
        assert config.auth_type == AuthType.CUSTOM_HEADER
        assert config.custom_header_name == "X-Custom-Auth"

    def test_custom_header_missing_name_raises(self) -> None:
        with pytest.raises(ValidationError, match="custom_header_name"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.CUSTOM_HEADER,
                custom_header_value="token-789",
            )

    def test_custom_header_missing_value_raises(self) -> None:
        with pytest.raises(ValidationError, match="custom_header_value"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.CUSTOM_HEADER,
                custom_header_name="X-Custom-Auth",
            )

    def test_none_auth_no_requirements(self) -> None:
        config = ProviderConfig(
            driver="litellm",
            auth_type=AuthType.NONE,
        )
        assert config.auth_type == AuthType.NONE

    def test_api_key_auth_requires_connection_name(self) -> None:
        """API-key auth has no embedded credential, so it mandates
        a ``connection_name`` referencing the catalog entry."""
        with pytest.raises(ValidationError, match="connection_name"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.API_KEY,
            )

    def test_api_key_auth_with_connection_name_valid(self) -> None:
        config = ProviderConfig(
            driver="litellm",
            auth_type=AuthType.API_KEY,
            connection_name="provider-test",
        )
        assert config.auth_type == AuthType.API_KEY
        assert config.connection_name == "provider-test"

    def test_oauth_with_optional_scope(self) -> None:
        config = ProviderConfig(
            driver="litellm",
            auth_type=AuthType.OAUTH,
            oauth_token_url="https://auth.example.com/token",
            oauth_client_id="client-123",
            oauth_client_secret="secret-456",
            oauth_scope="read write",
        )
        assert config.oauth_scope == "read write"

    def test_subscription_valid(self) -> None:
        config = ProviderConfig(
            driver="litellm",
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token="test-subscription-token",
            tos_accepted_at=_TOS_ACCEPTED,
        )
        assert config.auth_type == AuthType.SUBSCRIPTION
        assert config.subscription_token == "test-subscription-token"
        assert config.tos_accepted_at is not None

    def test_subscription_missing_token_raises(self) -> None:
        with pytest.raises(ValidationError, match="subscription_token"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.SUBSCRIPTION,
                tos_accepted_at=_TOS_ACCEPTED,
            )

    def test_subscription_missing_tos_raises(self) -> None:
        with pytest.raises(ValidationError, match="tos_accepted_at"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.SUBSCRIPTION,
                subscription_token="test-subscription-token",
            )

    def test_subscription_missing_both_raises(self) -> None:
        with pytest.raises(ValidationError, match="subscription_token"):
            ProviderConfig(
                driver="litellm",
                auth_type=AuthType.SUBSCRIPTION,
            )

    def test_litellm_provider_field(self) -> None:
        config = ProviderConfig(
            driver="litellm",
            connection_name="provider-test",
            litellm_provider="test-provider",
        )
        assert config.litellm_provider == "test-provider"

    def test_litellm_provider_defaults_to_none(self) -> None:
        config = ProviderConfig(driver="litellm", connection_name="provider-test")
        assert config.litellm_provider is None

    def test_api_key_auth_references_connection(self) -> None:
        """API-key auth references a catalog connection, not an embedded key."""
        config = ProviderConfig(
            driver="litellm",
            auth_type=AuthType.API_KEY,
            connection_name="provider-test-key-001",
        )
        assert config.auth_type == AuthType.API_KEY
        assert config.connection_name == "provider-test-key-001"
