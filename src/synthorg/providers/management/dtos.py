"""Provider-specific request/response DTOs.

Split from ``dto.py`` to keep that file under the 800-line limit.
"""

import re
from typing import TYPE_CHECKING, Self
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from synthorg.config.schema import (  # noqa: TC001
    LocalModelParams,
    ProviderModelConfig,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.providers.capabilities import ModelCapabilities  # noqa: TC001
from synthorg.providers.enums import AuthType

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderConfig


class ProviderModelResponse(BaseModel):
    """Model config enriched with runtime capabilities.

    Attributes:
        id: Model identifier.
        alias: Short alias for routing rules.
        cost_per_1k_input: Cost per 1k input tokens.
        cost_per_1k_output: Cost per 1k output tokens.
        max_context: Maximum context window size in tokens.
        estimated_latency_ms: Estimated median latency in milliseconds.
        local_params: Per-model launch parameters for local providers.
        supports_tools: Whether the model supports tool/function calling.
        supports_vision: Whether the model accepts image inputs.
        supports_streaming: Whether the model supports streaming responses.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(description="Model identifier")
    alias: NotBlankStr | None = Field(
        default=None,
        description="Short alias for routing rules",
    )
    cost_per_1k_input: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k input tokens",
    )
    cost_per_1k_output: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k output tokens",
    )
    max_context: int = Field(
        default=200_000,
        gt=0,
        description="Max context window in tokens",
    )
    estimated_latency_ms: int | None = Field(
        default=None,
        gt=0,
        le=300_000,
        description="Estimated median latency in ms",
    )
    local_params: LocalModelParams | None = Field(
        default=None,
        description="Per-model launch parameters for local providers",
    )
    supports_tools: bool = Field(
        default=False,
        description="Supports tool/function calling",
    )
    supports_vision: bool = Field(
        default=False,
        description="Accepts image inputs",
    )
    supports_streaming: bool = Field(
        default=True,
        description="Supports streaming responses",
    )


# ── Provider management DTOs ────────────────────────────────

_PROVIDER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
_RESERVED_PROVIDER_NAMES: frozenset[str] = frozenset(
    {"presets", "from-preset", "probe-local", "discovery-policy"},
)


def _validate_provider_name(v: str) -> str:
    """Validate a provider name against naming rules.

    Args:
        v: Candidate provider name.

    Returns:
        The validated name.

    Raises:
        ValueError: If the name is invalid or reserved.
    """
    if not _PROVIDER_NAME_PATTERN.match(v):
        msg = (
            "Provider name must be 2-64 chars, lowercase "
            "alphanumeric and hyphens, starting/ending with "
            "alphanumeric"
        )
        raise ValueError(msg)
    if v in _RESERVED_PROVIDER_NAMES:
        msg = f"Provider name {v!r} is reserved"
        raise ValueError(msg)
    return v


def _validate_base_url(v: str | None) -> str | None:
    """Validate that a base URL uses http or https scheme."""
    if v is None:
        return v
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        msg = f"base_url must use http or https scheme, got {parsed.scheme!r}"
        raise ValueError(msg)
    if not parsed.netloc:
        msg = "base_url must include a host"
        raise ValueError(msg)
    return v


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
        return _validate_provider_name(v)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        return _validate_base_url(v)


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
        return _validate_base_url(v)

    @model_validator(mode="after")
    def _validate_credential_clear_consistency(self) -> Self:
        """Reject simultaneous set and clear for credential fields."""
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


class TestConnectionResponse(BaseModel):
    """Result of a provider connection test.

    Attributes:
        success: Whether the connection test succeeded.
        latency_ms: Round-trip latency in milliseconds.
        error: Error message on failure.
        model_tested: Model ID that was tested.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    success: bool
    latency_ms: float | None = None
    error: NotBlankStr | None = None
    model_tested: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_success_error_consistency(self) -> Self:
        """Ensure success and error fields are consistent."""
        if self.success and self.error is not None:
            msg = "successful test must not have an error"
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "failed test must have an error message"
            raise ValueError(msg)
        return self


class ProviderResponse(BaseModel):
    """Safe provider config for API responses -- secrets stripped.

    Non-secret auth fields are included for frontend edit form UX.
    Boolean ``has_*`` indicators signal credential presence without
    exposing values.

    Attributes:
        driver: Driver backend name.
        litellm_provider: LiteLLM routing identifier override.
        auth_type: Authentication mechanism.
        base_url: Provider API base URL.
        models: Configured model definitions.
        has_api_key: Whether an API key is set.
        has_oauth_credentials: Whether OAuth credentials are configured.
        has_custom_header: Whether a custom auth header is configured.
        has_subscription_token: Whether a subscription token is set.
        tos_accepted_at: ISO timestamp of ToS acceptance (or ``None``).
        preset_name: Preset used to create this provider (if any).
        supports_model_pull: Whether pulling models is supported.
        supports_model_delete: Whether deleting models is supported.
        supports_model_config: Whether per-model config is supported.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    driver: NotBlankStr
    litellm_provider: NotBlankStr | None = None
    auth_type: AuthType
    base_url: NotBlankStr | None
    models: tuple[ProviderModelConfig, ...]
    has_api_key: bool
    has_oauth_credentials: bool
    has_custom_header: bool
    has_subscription_token: bool = False
    tos_accepted_at: str | None = None
    oauth_token_url: NotBlankStr | None = None
    oauth_client_id: NotBlankStr | None = None
    oauth_scope: NotBlankStr | None = None
    custom_header_name: NotBlankStr | None = None
    preset_name: NotBlankStr | None = None
    supports_model_pull: bool = False
    supports_model_delete: bool = False
    supports_model_config: bool = False


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
        return _validate_provider_name(v)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        return _validate_base_url(v)


class DiscoverModelsResponse(BaseModel):
    """Result of provider model auto-discovery.

    Attributes:
        discovered_models: Models found on the provider endpoint.
        provider_name: Name of the provider that was queried.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    discovered_models: tuple[ProviderModelConfig, ...]
    provider_name: NotBlankStr


class ProbePresetResponse(BaseModel):
    """Result of probing one preset's candidate URLs.

    Attributes:
        url: The first reachable base URL, or ``None`` if none responded.
        model_count: Number of models discovered at the URL.
        candidates_tried: Number of candidate URLs attempted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    url: NotBlankStr | None = None
    model_count: int = Field(default=0, ge=0)
    candidates_tried: int = Field(default=0, ge=0)


class ProbeLocalResponse(BaseModel):
    """Batch result of probing every local preset's candidate URLs.

    Attributes:
        results: Map of preset name to per-preset probe result.  Only
            local presets with non-empty ``candidate_urls`` are probed
            and appear here; cloud presets and vLLM (intentionally
            empty candidates) are excluded.
        errors: Map of preset name to error message for presets whose
            probes raised.  ``results`` and ``errors`` are disjoint:
            a successful probe for ``ollama`` populates ``results``,
            a raising probe populates ``errors``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    # Keys are preset names; ``NotBlankStr`` rejects empty / whitespace
    # entries that would otherwise sneak through ``dict[str, ...]`` and
    # render as ghost rows in the detected-list UI.
    results: dict[NotBlankStr, ProbePresetResponse] = Field(default_factory=dict)
    errors: dict[NotBlankStr, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_disjoint_results_errors(self) -> Self:
        """Enforce that ``results`` and ``errors`` share no preset name.

        A preset either succeeds (lands in ``results``) or fails
        (lands in ``errors``); both at once is a service-layer bug.
        Validating here moves the invariant from prose to type and
        catches misconstruction at the API boundary.

        ``frozen=True`` on the model blocks attribute reassignment but
        not mutation of the dict's contents -- in theory a caller
        could ``response.results["new"] = ...``.  We do not wrap
        ``results``/``errors`` in :class:`MappingProxyType` because
        Pydantic's msgspec serializer rejects ``mappingproxy`` at
        wire-encode time.  In practice the response is constructed in
        a single controller, returned through Litestar (which freezes
        nothing on the way out), and never re-handled by callers, so
        the dict-mutation hole is a theoretical-only concern.
        """
        overlap = set(self.results) & set(self.errors)
        if overlap:
            msg = (
                f"ProbeLocalResponse.results and .errors overlap on "
                f"preset(s): {sorted(overlap)!r}"
            )
            raise ValueError(msg)
        return self


def to_provider_response(config: ProviderConfig) -> ProviderResponse:
    """Convert a ProviderConfig to a safe ProviderResponse.

    Strips all secrets and provides boolean credential indicators.
    Resolves local model management capabilities from the preset
    when ``preset_name`` is set.

    Args:
        config: Provider configuration (may contain secrets).

    Returns:
        Safe response DTO with secrets stripped.
    """
    from synthorg.providers.presets import (  # noqa: PLC0415
        LocalPreset,
        get_preset,
    )

    tos_str = (
        config.tos_accepted_at.isoformat()
        if config.tos_accepted_at is not None
        else None
    )
    preset = get_preset(config.preset_name) if config.preset_name else None
    # Local-management capability flags (pull/delete/config) live only
    # on LocalPreset and are exposed back to the dashboard through this
    # ProviderResponse DTO.  Cloud providers default them to False.
    local_preset = preset if isinstance(preset, LocalPreset) else None
    return ProviderResponse(
        driver=config.driver,
        litellm_provider=config.litellm_provider,
        auth_type=config.auth_type,
        base_url=config.base_url,
        models=config.models,
        has_api_key=config.api_key is not None,
        has_oauth_credentials=(
            config.oauth_client_id is not None
            and config.oauth_client_secret is not None
            and config.oauth_token_url is not None
        ),
        has_custom_header=(
            config.custom_header_name is not None
            and config.custom_header_value is not None
        ),
        has_subscription_token=config.subscription_token is not None,
        tos_accepted_at=tos_str,
        oauth_token_url=config.oauth_token_url,
        oauth_client_id=config.oauth_client_id,
        oauth_scope=config.oauth_scope,
        custom_header_name=config.custom_header_name,
        preset_name=config.preset_name,
        supports_model_pull=local_preset.supports_model_pull if local_preset else False,
        supports_model_delete=local_preset.supports_model_delete
        if local_preset
        else False,
        supports_model_config=local_preset.supports_model_config
        if local_preset
        else False,
    )


# ── Enriched model response ─────────────────────────────────


def to_provider_model_response(
    config: ProviderModelConfig,
    capabilities: ModelCapabilities | None = None,
) -> ProviderModelResponse:
    """Convert a ProviderModelConfig to an enriched response.

    When *capabilities* is provided, capability booleans are overlaid.
    Otherwise, defaults are used.

    Args:
        config: Model configuration from provider config.
        capabilities: Runtime capabilities from the driver layer.

    Returns:
        Enriched model response DTO.
    """
    return ProviderModelResponse(
        id=config.id,
        alias=config.alias,
        cost_per_1k_input=config.cost_per_1k_input,
        cost_per_1k_output=config.cost_per_1k_output,
        max_context=config.max_context,
        estimated_latency_ms=config.estimated_latency_ms,
        local_params=config.local_params,
        supports_tools=(
            capabilities.supports_tools if capabilities is not None else False
        ),
        supports_vision=(
            capabilities.supports_vision if capabilities is not None else False
        ),
        supports_streaming=(
            capabilities.supports_streaming if capabilities is not None else True
        ),
    )


# ── Local model management DTOs ──────────────────────────────


_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9._:/@-]+$")


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
