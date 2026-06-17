# module-kind: declarative
"""Provider-management response DTOs.

Extracted from ``dtos.py``. Outbound response shapes for the provider
management surface; secrets are never carried here.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from synthorg.budget.currency import (
    DEFAULT_CURRENCY,
    CurrencyCode,
)
from synthorg.config.model_staleness import ModelStaleness
from synthorg.config.schema import (
    LocalModelParams,
    ProviderModelConfig,
)
from synthorg.core.types import NotBlankStr
from synthorg.providers.enums import AuthType


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    currency: CurrencyCode = Field(
        default=DEFAULT_CURRENCY,
        description=(
            "Currency the cost fields are expressed in.  Carries the "
            "operator's configured ``budget.currency`` so aggregation "
            "sites can enforce the same-currency invariant without a "
            "second lookup."
        ),
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
    family: NotBlankStr | None = Field(
        default=None,
        description="Parsed model family (groups models for the picker)",
    )
    stale: ModelStaleness | None = Field(
        default=None,
        description="Staleness marker when the id left the live catalogue",
    )


class TestConnectionResponse(BaseModel):
    """Result of a provider connection test.

    Attributes:
        success: Whether the connection test succeeded.
        latency_ms: Round-trip latency in milliseconds.
        error: Error message on failure.
        model_tested: Model ID that was tested.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    success: bool
    latency_ms: float | None = None
    error: NotBlankStr | None = None
    model_tested: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_success_error_consistency(self) -> Self:
        """Ensure success and error fields are consistent.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If a successful response has an error message, or
                a failed response has no error message.
        """
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # Provider identifier. Populated by paginated list responses
    # (``list_providers`` walks the configs dict by key and threads the
    # name through ``to_provider_response(name=...)``) so cursor
    # consumers can rebuild the dict-by-name index without relying on
    # collection ordering. Single-provider GET-by-path responses leave
    # this ``None`` because the URL already carries the identifier;
    # consumers should fall back to the path parameter in that case.
    name: NotBlankStr | None = None
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


class DiscoverModelsResponse(BaseModel):
    """Result of provider model auto-discovery.

    Attributes:
        discovered_models: Models found on the provider endpoint.
        provider_name: Name of the provider that was queried.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    discovered_models: tuple[ProviderModelConfig, ...]
    provider_name: NotBlankStr


class ProbePresetResponse(BaseModel):
    """Result of probing one preset's candidate URLs.

    Attributes:
        url: The first reachable base URL, or ``None`` if none responded.
        model_count: Number of models discovered at the URL.
        candidates_tried: Number of candidate URLs attempted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    url: NotBlankStr | None = None
    model_count: int = Field(default=0, ge=0)
    candidates_tried: int = Field(default=0, ge=0)


class ProbeLocalResponse(BaseModel):
    """Batch result of probing every local preset's candidate URLs.

    Attributes:
        results: Map of preset name to per-preset probe result.  Only
            local presets with non-empty ``candidate_urls`` are probed
            and appear here; cloud presets and any local runtime that
            ships intentionally-empty candidates are excluded.
        errors: Map of preset name to error message for presets whose
            probes raised.  ``results`` and ``errors`` are disjoint:
            a successful probe for a preset populates ``results``,
            a raising probe populates ``errors``.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
        # ``MappingProxyType`` instances are stored on the model after
        # ``_freeze_mappings`` runs; Pydantic-core needs the lenient
        # arbitrary-type allowance to keep them past validation.
        arbitrary_types_allowed=True,
    )

    # Keys are preset names; ``NotBlankStr`` rejects empty / whitespace
    # entries that would otherwise sneak through ``dict[str, ...]`` and
    # render as ghost rows in the detected-list UI.
    results: Mapping[NotBlankStr, ProbePresetResponse] = Field(default_factory=dict)
    errors: Mapping[NotBlankStr, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_disjoint_results_errors(self) -> Self:
        """Enforce disjointness and freeze ``results`` / ``errors``.

        A preset either succeeds (lands in ``results``) or fails
        (lands in ``errors``); both at once is a service-layer bug.
        After the disjointness check passes, both mappings are wrapped
        in :class:`MappingProxyType` so ``frozen=True`` on the model
        blocks attribute reassignment AND in-place mutation of the
        mapping contents (e.g. ``response.results["new"] = ...``).
        The ``_serialize_mappings`` field-serializer below unwraps back
        to plain dicts at JSON-encode time so msgspec / pydantic-core
        serialization still succeeds.

        Returns:
            The validated instance with ``results`` and ``errors`` both
            wrapped in ``MappingProxyType``.

        Raises:
            ValueError: If ``results`` and ``errors`` share any preset
                key (a preset cannot both succeed and fail).
        """
        overlap = set(self.results) & set(self.errors)
        if overlap:
            msg = (
                f"ProbeLocalResponse.results and .errors overlap on "
                f"preset(s): {sorted(overlap)!r}"
            )
            raise ValueError(msg)
        if not isinstance(self.results, MappingProxyType):
            object.__setattr__(
                self,
                "results",
                MappingProxyType(dict(self.results)),
            )
        if not isinstance(self.errors, MappingProxyType):
            object.__setattr__(
                self,
                "errors",
                MappingProxyType(dict(self.errors)),
            )
        return self

    @field_serializer("results", "errors")
    def _serialize_mappings(
        self,
        value: Mapping[str, ProbePresetResponse | str],
    ) -> dict[str, ProbePresetResponse | str]:
        """Unwrap ``MappingProxyType`` to plain ``dict`` for JSON encode.

        Returns:
            A plain ``dict`` copy (``MappingProxyType`` is not JSON-encodable).
        """
        return dict(value)
