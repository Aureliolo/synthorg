# module-kind: declarative
"""Provider-management request DTOs.

Extracted from ``dtos.py``. Inbound payloads for the provider
management surface; field validators delegate to
``_provider_validators``.
"""

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from synthorg.config.schema import (
    LocalModelParams,
    ProviderModelConfig,
)
from synthorg.core.types import NotBlankStr
from synthorg.providers.enums import AuthType
from synthorg.providers.management._provider_validators import (
    _MODEL_NAME_RE,
    _reject_blank_secret,
    _validate_base_url,
    _validate_oauth_token_url,
    _validate_provider_name,
)


class CreateProviderRequest(BaseModel):
    """Payload for creating a new provider.

    Attributes:
        name: Unique provider name (2-64 chars, lowercase + hyphens).
        driver: Driver backend name (default ``"litellm"``).
        litellm_provider: LiteLLM routing identifier override.
        auth_type: Authentication mechanism for this provider.
        api_key: API key credential (optional, depends on auth_type).
        subscription_token: Bearer token for subscription-based auth.
        tos_accepted: Whether the user accepted the subscription ToS.
        base_url: Provider API base URL.
        models: Pre-configured model definitions.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    name: NotBlankStr = Field(max_length=64)
    driver: NotBlankStr = "litellm"
    litellm_provider: NotBlankStr | None = None
    auth_type: AuthType = AuthType.API_KEY
    api_key: SecretStr | None = None
    subscription_token: SecretStr | None = None
    tos_accepted: bool = False
    base_url: NotBlankStr | None = None
    oauth_token_url: NotBlankStr | None = None
    oauth_client_id: NotBlankStr | None = None
    oauth_client_secret: SecretStr | None = None
    oauth_scope: NotBlankStr | None = None
    custom_header_name: NotBlankStr | None = None
    custom_header_value: SecretStr | None = None
    models: tuple[ProviderModelConfig, ...] = ()
    preset_name: NotBlankStr | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Validate the provider name field.

        Returns:
            The validated provider name.
        """
        return _validate_provider_name(v)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        """Validate the base URL field.

        Returns:
            The validated base URL, or ``None``.
        """
        return _validate_base_url(v)

    @field_validator("oauth_token_url")
    @classmethod
    def _validate_oauth_token_url(cls, v: str | None) -> str | None:
        """Validate the OAuth token URL field.

        Returns:
            The validated OAuth token URL, or ``None``.
        """
        return _validate_oauth_token_url(v)

    @field_validator("api_key")
    @classmethod
    def _check_api_key(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``api_key``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="api_key")

    @field_validator("subscription_token")
    @classmethod
    def _check_subscription_token(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``subscription_token``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="subscription_token")

    @field_validator("oauth_client_secret")
    @classmethod
    def _check_oauth_client_secret(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``oauth_client_secret``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="oauth_client_secret")

    @field_validator("custom_header_value")
    @classmethod
    def _check_custom_header_value(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``custom_header_value``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="custom_header_value")


class UpdateProviderRequest(BaseModel):
    """Payload for updating a provider (partial update).

    All fields are optional -- only provided fields are updated.
    ``tos_accepted``: only ``True`` re-stamps the timestamp;
    ``False`` and ``None`` are no-ops (cannot be retracted).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    driver: NotBlankStr | None = None
    litellm_provider: NotBlankStr | None = None
    auth_type: AuthType | None = None
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    subscription_token: SecretStr | None = None
    clear_subscription_token: bool = False
    tos_accepted: bool | None = None
    base_url: NotBlankStr | None = None
    oauth_token_url: NotBlankStr | None = None
    oauth_client_id: NotBlankStr | None = None
    oauth_client_secret: SecretStr | None = None
    oauth_scope: NotBlankStr | None = None
    custom_header_name: NotBlankStr | None = None
    custom_header_value: SecretStr | None = None
    models: tuple[ProviderModelConfig, ...] | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        """Validate the base URL field.

        Returns:
            The validated base URL, or ``None``.
        """
        return _validate_base_url(v)

    @field_validator("oauth_token_url")
    @classmethod
    def _validate_oauth_token_url(cls, v: str | None) -> str | None:
        """Validate the OAuth token URL field.

        Returns:
            The validated OAuth token URL, or ``None``.
        """
        return _validate_oauth_token_url(v)

    @field_validator("api_key")
    @classmethod
    def _check_api_key(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``api_key``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="api_key")

    @field_validator("subscription_token")
    @classmethod
    def _check_subscription_token(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``subscription_token``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="subscription_token")

    @field_validator("oauth_client_secret")
    @classmethod
    def _check_oauth_client_secret(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``oauth_client_secret``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="oauth_client_secret")

    @field_validator("custom_header_value")
    @classmethod
    def _check_custom_header_value(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``custom_header_value``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="custom_header_value")

    @model_validator(mode="after")
    def _validate_credential_clear_consistency(self) -> Self:
        """Reject simultaneous set and clear for credential fields.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``api_key`` and ``clear_api_key``, or
                ``subscription_token`` and ``clear_subscription_token``,
                are both set.
        """
        if self.api_key is not None and self.clear_api_key:
            msg = "api_key and clear_api_key are mutually exclusive"
            raise ValueError(msg)
        if self.subscription_token is not None and self.clear_subscription_token:
            msg = (
                "subscription_token and clear_subscription_token are mutually exclusive"
            )
            raise ValueError(msg)
        return self


class TestConnectionRequest(BaseModel):
    """Payload for testing a provider connection.

    Attributes:
        model: Model to test (defaults to first model in config).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    model: NotBlankStr | None = None


class CreateFromPresetRequest(BaseModel):
    """Payload for creating a provider from a preset.

    Attributes:
        preset_name: Name of the preset to create from.
        name: Unique provider name (2-64 chars, lowercase + hyphens).
        auth_type: Override the preset's default auth type (optional).
        subscription_token: Bearer token for subscription-based auth.
        tos_accepted: Whether the user accepted the subscription ToS.
        base_url: Override the preset's default base URL (optional).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    preset_name: NotBlankStr
    name: NotBlankStr = Field(max_length=64)
    auth_type: AuthType | None = None
    api_key: SecretStr | None = None
    subscription_token: SecretStr | None = None
    tos_accepted: bool = False
    base_url: NotBlankStr | None = None
    models: tuple[ProviderModelConfig, ...] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Validate the provider name field.

        Returns:
            The validated provider name.
        """
        return _validate_provider_name(v)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        """Validate the base URL field.

        Returns:
            The validated base URL, or ``None``.
        """
        return _validate_base_url(v)

    @field_validator("api_key")
    @classmethod
    def _check_api_key(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``api_key``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="api_key")

    @field_validator("subscription_token")
    @classmethod
    def _check_subscription_token(cls, v: SecretStr | None) -> SecretStr | None:
        """Reject a blank ``subscription_token``.

        Returns:
            The validated secret, or ``None``.
        """
        return _reject_blank_secret(v, field="subscription_token")


class PullModelRequest(BaseModel):
    """Payload for pulling a model on a local provider.

    Attributes:
        model_name: Model identifier to pull (e.g. ``"test-local-001:latest"``).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    model_name: NotBlankStr = Field(
        max_length=256,
        description="Model name/tag to pull",
    )

    @field_validator("model_name")
    @classmethod
    def _validate_model_name(cls, v: str) -> str:
        """Validate the model-name character set.

        Returns:
            The validated model name.

        Raises:
            ValueError: If *v* contains characters outside the allowed
                set (alphanumerics, ``._:/@-``).
        """
        if not _MODEL_NAME_RE.match(v):
            msg = (
                "model_name must contain only alphanumeric characters, "
                "dots, underscores, colons, slashes, hyphens, and @"
            )
            raise ValueError(msg)
        return v


class UpdateModelConfigRequest(BaseModel):
    """Payload for updating per-model launch parameters.

    Attributes:
        local_params: New launch parameters for the model.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    local_params: LocalModelParams
