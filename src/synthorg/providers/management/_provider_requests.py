# module-kind: declarative
"""Provider-management request DTOs.

Inbound payloads for the provider management surface; field validators
delegate to ``_provider_validators``.
"""

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
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
    ValidatedApiKey,
    ValidatedBaseUrl,
    ValidatedCustomHeaderValue,
    ValidatedOAuthClientSecret,
    ValidatedOAuthTokenUrl,
    ValidatedProviderName,
    ValidatedSubscriptionToken,
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

    name: ValidatedProviderName = Field(
        max_length=64,
        description="Unique provider name (2-64 chars, lowercase + hyphens).",
        examples=["example-provider"],
    )
    driver: NotBlankStr = Field(
        default="litellm",
        description="Driver backend name.",
        examples=["litellm"],
    )
    litellm_provider: NotBlankStr | None = Field(
        default=None,
        description="LiteLLM routing identifier override, if needed.",
    )
    auth_type: AuthType = Field(
        default=AuthType.API_KEY,
        description="Authentication mechanism the provider uses.",
    )
    # Secret fields carry a description but never an example, so no
    # credential-shaped placeholder leaks into the rendered OpenAPI spec.
    api_key: ValidatedApiKey = Field(
        default=None,
        description="API key credential (required for API_KEY auth).",
    )
    subscription_token: ValidatedSubscriptionToken = Field(
        default=None,
        description="Bearer token for subscription-based auth.",
    )
    tos_accepted: bool = Field(
        default=False,
        description="Whether the operator accepted the subscription terms.",
    )
    base_url: ValidatedBaseUrl = Field(
        default=None,
        description="Provider API base URL.",
        examples=["https://api.example-provider.test/v1"],
    )
    keep_alive: NotBlankStr | None = None
    oauth_token_url: ValidatedOAuthTokenUrl = None
    oauth_client_id: NotBlankStr | None = None
    oauth_client_secret: ValidatedOAuthClientSecret = None
    oauth_scope: NotBlankStr | None = None
    custom_header_name: NotBlankStr | None = None
    custom_header_value: ValidatedCustomHeaderValue = None
    models: tuple[ProviderModelConfig, ...] = ()
    preset_name: NotBlankStr | None = None
    agent_eligible: bool = Field(
        default=True,
        description="Whether this provider may back an agent (seeded onto "
        "agents and picked by stakes routing). False keeps it usable for "
        "explicitly-configured feature calls but excludes it from all agent "
        "assignment.",
    )


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
    api_key: ValidatedApiKey = None
    clear_api_key: bool = False
    subscription_token: ValidatedSubscriptionToken = None
    clear_subscription_token: bool = False
    tos_accepted: bool | None = None
    base_url: ValidatedBaseUrl = None
    keep_alive: NotBlankStr | None = None
    oauth_token_url: ValidatedOAuthTokenUrl = None
    oauth_client_id: NotBlankStr | None = None
    oauth_client_secret: ValidatedOAuthClientSecret = None
    oauth_scope: NotBlankStr | None = None
    custom_header_name: NotBlankStr | None = None
    custom_header_value: ValidatedCustomHeaderValue = None
    models: tuple[ProviderModelConfig, ...] | None = None
    agent_eligible: bool | None = None

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
    name: ValidatedProviderName = Field(max_length=64)
    auth_type: AuthType | None = None
    api_key: ValidatedApiKey = None
    subscription_token: ValidatedSubscriptionToken = None
    tos_accepted: bool = False
    base_url: ValidatedBaseUrl = None
    models: tuple[ProviderModelConfig, ...] | None = None


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
