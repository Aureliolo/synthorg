"""Provider and model configuration schemas.

Extracted from :mod:`synthorg.config.schema` to keep the root schema
module under the project size limit.
"""

from collections import Counter
from typing import ClassVar, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.budget.quota import DegradationConfig, SubscriptionConfig
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.model_staleness import ModelStaleness
from synthorg.core.billing_enums import BillingModel
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.providers.enums import AuthType
from synthorg.providers.vram_guard_config import OllamaVramGuardConfig

logger = get_logger(__name__)


class LocalModelParams(BaseModel):
    """Per-model launch parameters for local providers."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    num_ctx: int | None = Field(default=None, gt=0)
    num_gpu_layers: int | None = Field(default=None, ge=0)
    num_threads: int | None = Field(default=None, gt=0)
    num_batch: int | None = Field(default=None, gt=0)
    repeat_penalty: float | None = Field(
        default=None,
        gt=0.0,
        description="Repetition penalty",
    )


class ModelCapabilityOverrides(BaseModel):
    """Operator-declared capability overrides for one model.

    ``extract_model_metadata`` falls back per field to persisted metadata
    when a provider's card is silent on a capability, but a card can also be
    silent because the provider has no card at all (Ollama, an unlisted
    model): there is no probe result to fall back to, and the feature stays
    off with no path to enable it. Each field here is a three-state
    override applied on top of the fully-resolved capability in
    ``build_capabilities``: ``True``/``False`` forces the value regardless
    of what the card or probe reported, and ``None`` (the default, and the
    explicit-null wire value) means no override -- the resolved value
    stands. The operator who knows their own model gets the final word,
    per the config precedence ladder (card, then operator).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    supports_tools: bool | None = Field(default=None)
    supports_vision: bool | None = Field(default=None)
    supports_streaming: bool | None = Field(default=None)
    supports_embeddings: bool | None = Field(default=None)
    supports_image_generation: bool | None = Field(default=None)
    supports_reasoning: bool | None = Field(default=None)
    supports_prompt_caching: bool | None = Field(default=None)


class ProviderModelConfig(BaseModel):
    """Configuration for a single LLM model within a provider."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Model identifier")
    alias: NotBlankStr | None = Field(
        default=None,
        description="Short alias for routing rules",
    )
    cost_per_1k_input: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k input tokens (base currency)",
    )
    cost_per_1k_output: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k output tokens (base currency)",
    )
    cost_per_image: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Flat cost per generated image (base currency) for image-output "
            "models; None for chat/embedding models. Operator-owned, kept "
            "aligned with the provider's per-image price like the token costs."
        ),
    )
    max_context: int = Field(
        default=200_000,
        gt=0,
        description="Maximum context window size in tokens",
    )
    estimated_latency_ms: int | None = Field(
        default=None,
        gt=0,
        le=300_000,
        description="Estimated median latency in milliseconds",
    )
    local_params: LocalModelParams | None = Field(
        default=None,
        description="Per-model launch parameters for local providers",
    )
    metadata: ModelMetadata = Field(
        default_factory=ModelMetadata,
        description="Capability and family/generation metadata (enriched at ingest)",
    )
    stale: ModelStaleness | None = Field(
        default=None,
        description=(
            "Set by the periodic model-refresh service when the id is no "
            "longer advertised by its provider; None means current."
        ),
    )
    capability_overrides: ModelCapabilityOverrides | None = Field(
        default=None,
        description=(
            "Operator-declared capability overrides applied on top of the "
            "resolved metadata; None means no override on any field."
        ),
    )


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    driver: NotBlankStr = Field(
        default="litellm",
        description="Driver backend name",
    )
    litellm_provider: NotBlankStr | None = Field(
        default=None,
        description=(
            "LiteLLM provider identifier for routing "
            "(e.g. 'example-provider').  Falls back to "
            "the provider name when None."
        ),
    )
    family: NotBlankStr | None = Field(
        default=None,
        description=(
            "Provider family for cross-validation grouping "
            "(e.g. 'provider-family-a', 'provider-family-b').  "
            "When None, the provider name is used as the family."
        ),
    )
    auth_type: AuthType = Field(
        default=AuthType.API_KEY,
        description="Authentication type",
    )
    connection_name: NotBlankStr | None = Field(
        default=None,
        repr=False,
        description=(
            "Reference to a ConnectionCatalog entry.  Credentials are "
            "resolved from the catalog at runtime; required for API-key "
            "auth, which has no embedded credential field."
        ),
    )
    subscription_token: NotBlankStr | None = Field(
        default=None,
        repr=False,
        description="Bearer token for subscription-based auth",
    )
    tos_accepted_at: AwareDatetime | None = Field(
        default=None,
        description="When subscription ToS was accepted",
    )
    base_url: NotBlankStr | None = Field(
        default=None,
        description="Base URL for the provider API",
    )
    keep_alive: NotBlankStr | None = Field(
        default=None,
        description=(
            "Ollama keep_alive: how long the server keeps a model loaded "
            "after a request (e.g. '5m', '0' to unload immediately, '-1' to "
            "keep forever). Sent only to ollama providers; unset falls back "
            "to the driver's bounded default (5m) rather than the ollama "
            "server's OLLAMA_KEEP_ALIVE."
        ),
    )
    vram_guard: OllamaVramGuardConfig = Field(
        default_factory=OllamaVramGuardConfig,
        description=(
            "VRAM-aware model load/eviction guard for ollama providers "
            "(ignored by other providers)."
        ),
    )
    oauth_token_url: NotBlankStr | None = Field(
        default=None,
        description="OAuth token endpoint URL",
    )
    oauth_client_id: NotBlankStr | None = Field(
        default=None,
        description="OAuth client identifier",
    )
    oauth_client_secret: NotBlankStr | None = Field(
        default=None,
        repr=False,
        description="OAuth client secret",
    )
    oauth_scope: NotBlankStr | None = Field(
        default=None,
        description="OAuth scope string",
    )
    custom_header_name: NotBlankStr | None = Field(
        default=None,
        description="Name of custom auth header",
    )
    custom_header_value: NotBlankStr | None = Field(
        default=None,
        repr=False,
        description="Value of custom auth header",
    )
    models: tuple[ProviderModelConfig, ...] = Field(
        default=(),
        description="Available models",
    )
    retry: RetryConfig = Field(
        default_factory=RetryConfig,
        description="Retry configuration for transient errors",
    )
    rate_limiter: RateLimiterConfig = Field(
        default_factory=RateLimiterConfig,
        description="Client-side rate limiting configuration",
    )
    subscription: SubscriptionConfig = Field(
        default_factory=SubscriptionConfig,
        description="Subscription and quota configuration",
    )
    degradation: DegradationConfig = Field(
        default_factory=DegradationConfig,
        description="Degradation strategy when quota exhausted",
    )
    preset_name: NotBlankStr | None = Field(
        default=None,
        description="Preset used to create this provider (if any)",
    )
    billing_model: BillingModel = Field(
        default=BillingModel.UNKNOWN,
        description=(
            "How this connection charges, which decides whether a "
            "money-denominated spend ceiling can measure anything against it. "
            "Seeded from the preset at create time and settable afterwards: "
            "the operator knows their own contract better than a shipped "
            "table does, and a provider built from no preset has no other "
            "source. UNKNOWN is treated as unmeasurable rather than as "
            "per-token, because assuming a ceiling binds when it may not is "
            "the failure this field exists to remove"
        ),
    )
    agent_eligible: bool = Field(
        default=True,
        description=(
            "Whether this provider may back an agent: its models are seeded "
            "onto agents at provisioning and picked by stakes routing. When "
            "False the provider is excluded from new automatic seeding, stakes "
            "routing, and provider-agnostic reselection, but stays fully usable "
            "for explicitly-configured feature calls (chat / judge / charter "
            "models the operator sets). It is NOT an immediate traffic cutover: "
            "an agent already pinned to this provider keeps running on it (the "
            "exclusive binding is honoured) until it is reassigned. Lets an "
            "operator stop new agents sourcing from a gateway."
        ),
    )

    _AUTH_REQUIRED_FIELDS: ClassVar[dict[AuthType, tuple[str, ...]]] = {
        AuthType.OAUTH: (
            "oauth_token_url",
            "oauth_client_id",
            "oauth_client_secret",
        ),
        AuthType.CUSTOM_HEADER: (
            "custom_header_name",
            "custom_header_value",
        ),
        AuthType.SUBSCRIPTION: (
            "subscription_token",
            "tos_accepted_at",
        ),
    }
    _CONNECTION_REQUIRED_AUTH_TYPES: ClassVar[frozenset[AuthType]] = frozenset(
        {AuthType.API_KEY},
    )

    @model_validator(mode="after")
    def _validate_auth_fields(self) -> Self:
        """Validate auth fields based on auth_type.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``connection_name`` is absent for an auth type
                that resolves its only credential from the catalog, or when
                a non-connection auth type is missing a required embedded
                credential field.
        """
        # The ``connection_name`` short-circuit is scoped to catalog-backed
        # auth types only. A non-catalog auth type (OAUTH / CUSTOM_HEADER /
        # SUBSCRIPTION) carrying a ``connection_name`` must still satisfy its
        # embedded-field requirements -- otherwise the reference would let it
        # bypass ``_AUTH_REQUIRED_FIELDS`` and persist an unauthable config.
        if self.auth_type in self._CONNECTION_REQUIRED_AUTH_TYPES:
            if self.connection_name is not None:
                return self
            label = self.auth_type.value.replace("_", " ").title()
            msg = f"{label} auth_type requires: connection_name"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ProviderConfig",
                error=msg,
            )
            raise ValueError(msg)
        required = self._AUTH_REQUIRED_FIELDS.get(self.auth_type)
        if required is None:
            return self
        missing = [f for f in required if getattr(self, f) is None]
        if missing:
            label = self.auth_type.value.replace("_", " ").title()
            msg = f"{label} auth_type requires: {', '.join(missing)}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ProviderConfig",
                error=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_model_identifiers(self) -> Self:
        """Ensure model IDs and aliases are each unique and non-overlapping.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When two models share an id, two models share a non-null
                alias, or one model's alias equals a different model's id.
        """
        ids = [m.id for m in self.models]
        if len(ids) != len(set(ids)):
            dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
            msg = f"Duplicate model IDs: {dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ProviderConfig",
                error=msg,
            )
            raise ValueError(msg)
        aliases = [m.alias for m in self.models if m.alias is not None]
        if len(aliases) != len(set(aliases)):
            dupes = sorted(a for a, c in Counter(aliases).items() if c > 1)
            msg = f"Duplicate model aliases: {dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ProviderConfig",
                error=msg,
            )
            raise ValueError(msg)
        # A ref that is one model's alias AND another model's id is ambiguous:
        # a provider-scoped resolve (resolve_for_pair) returns the first match
        # by declaration order, which could silently bind an agent to the wrong
        # model. The exclusive (provider, model) contract requires every ref to
        # be unambiguous within a provider.
        id_set = set(ids)
        colliding = sorted(
            m.alias
            for m in self.models
            if m.alias is not None and m.alias != m.id and m.alias in id_set
        )
        if colliding:
            msg = f"Model aliases collide with another model's id: {colliding}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ProviderConfig",
                error=msg,
            )
            raise ValueError(msg)
        return self
