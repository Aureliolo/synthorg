"""DTOs for the post-CRUD Provider/Model capabilities.

Carved out of ``dto_providers.py`` to keep both modules under the
800-line cap.  This module owns the wire shapes for:

* the audit log (:class:`ProviderAuditEvent`),
* rate-limit overrides (:class:`RateLimitsResponse`,
  :class:`RateLimitsUpdateRequest`),
* preset overrides (:class:`PresetOverride`,
  :class:`PresetOverrideUpdateRequest`),
* credential rotation (:data:`CredentialsRotateRequest`, a
  discriminated union),
* manual model add (:class:`AddModelRequest`),
* bulk model sync (:class:`SyncModelsRequest`,
  :class:`SyncModelsResponse`).

These DTOs are surfaced via the controller routes added in the same
PR; the existing CRUD DTOs (create / update / delete provider, pull
model, ...) stay in ``dto_providers.py``.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)

from synthorg.config.schema import ProviderModelConfig  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.providers.enums import (
    AuthType,  # noqa: TC001 -- runtime literal discriminator
)

# ── Provider audit log ────────────────────────────────────────────────


# Stable string set for ProviderAuditEvent.event_type. Adding a new
# event also requires updating the corresponding hook in
# ``synthorg.providers.management.audit_service`` so the new event
# emits an audit row.
ProviderAuditEventType = Literal[
    "provider_created",
    "provider_updated",
    "provider_deleted",
    "provider_credentials_rotated",
    "provider_rate_limits_updated",
    "preset_override_updated",
    "model_added",
    "model_removed",
    "model_config_updated",
    "model_pulled",
    "models_synced",
]
"""Event-type discriminator for ``ProviderAuditEvent``.

Audit rows describe the *category* of mutation; the per-event
metadata (model id, override fields touched, masked credential
prefix, ...) lives in :attr:`ProviderAuditEvent.payload`. Order
matches the rough lifecycle order so reviewers can scan a literal
audit dump top-to-bottom.
"""


class ProviderAuditActor(BaseModel):
    """Minimal actor descriptor for a provider audit row.

    Captures who performed a mutation at the moment it happened.
    ``id`` is the actor's stable identifier (typically a user id from
    the auth layer) and ``label`` is a display string (username or
    role). Both are required so audit rows always identify the actor;
    machine-driven mutations (background bootstrap, hot-reload from
    file) populate ``id="system"`` with a descriptive label.

    Attributes:
        id: Stable identifier for the actor.
        label: Human-readable display label (username, role, ...).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(description="Stable actor identifier")
    label: NotBlankStr = Field(description="Human-readable actor label")


class ProviderAuditEvent(BaseModel):
    """One row in the provider mutation audit log (append-only).

    Audit rows are written by ``ProviderAuditService.record(...)`` from
    every mutation entry point on ``ProviderManagementService`` so the
    UI's audit drawer (``GET /api/v1/providers/{name}/audit``) can
    surface a queryable history.

    The persistence layer assigns the integer ``id`` (autoincrement);
    DTOs constructed in tests can leave it ``None`` and the repo layer
    will fill it on save.

    ``payload`` is event-type-specific structured metadata. Senders
    MUST keep credentials masked (``"prefix***last4"``) -- the audit
    row is read-only forever and any plaintext leak is permanent. The
    SEC-1 secret-log rule applies here.

    Attributes:
        id: Monotonic row identifier assigned by persistence on save.
        provider_name: The provider the mutation targeted.
        event_type: Mutation category (see :data:`ProviderAuditEventType`).
        actor: Who performed the mutation.
        payload: Event-specific metadata, JSON-serialisable.
        occurred_at: UTC timestamp of the mutation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: int | None = Field(default=None, ge=1, description="Repo-assigned row id")
    provider_name: NotBlankStr = Field(description="Provider name the mutation targets")
    event_type: ProviderAuditEventType = Field(description="Mutation category")
    actor: ProviderAuditActor = Field(description="Actor performing the mutation")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific metadata; credentials must be masked",
    )
    occurred_at: AwareDatetime = Field(description="UTC timestamp of the mutation")


# ── Rate-limit override ───────────────────────────────────────────────


class RateLimitsResponse(BaseModel):
    """Effective rate-limit configuration for one provider.

    Attributes:
        requests_per_minute: Per-provider RPM cap (``None`` = unlimited).
        requests_per_hour: Per-provider RPH cap (``None`` = unlimited).
        concurrent_requests: Max concurrent in-flight requests
            (``None`` = unlimited).
        tokens_per_minute: Per-provider TPM cap (``None`` = unlimited).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    requests_per_minute: int | None = Field(
        default=None,
        ge=1,
        description="Per-provider RPM cap; null = unlimited",
    )
    requests_per_hour: int | None = Field(
        default=None,
        ge=1,
        description="Per-provider RPH cap; null = unlimited",
    )
    concurrent_requests: int | None = Field(
        default=None,
        ge=1,
        description="Max concurrent in-flight requests; null = unlimited",
    )
    tokens_per_minute: int | None = Field(
        default=None,
        ge=1,
        description="Per-provider TPM cap; null = unlimited",
    )


class RateLimitsUpdateRequest(BaseModel):
    """Partial-update payload for ``PATCH /providers/{name}/rate-limits``.

    Every field is optional; ``None`` means "leave unchanged" rather
    than "set to unlimited" -- callers send the explicit ``Unset``
    sentinel via ``model_dump(exclude_unset=True)`` to disambiguate.
    At least one field MUST be set; an empty patch is HTTP 422 (callers
    should not call PATCH without intent to change something).

    To set a limit to "unlimited", pass ``-1`` (sentinel) which the
    service layer converts to ``None`` on the persisted record.  The
    DTO accepts ``-1`` as the only non-``None`` non-positive value.

    Attributes:
        requests_per_minute: New RPM cap, ``-1`` for unlimited, or
            unset to leave unchanged.
        requests_per_hour: New RPH cap, ``-1`` for unlimited, or unset.
        concurrent_requests: New concurrent cap, ``-1`` for unlimited,
            or unset.
        tokens_per_minute: New TPM cap, ``-1`` for unlimited, or unset.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    requests_per_minute: int | None = Field(default=None, ge=-1)
    requests_per_hour: int | None = Field(default=None, ge=-1)
    concurrent_requests: int | None = Field(default=None, ge=-1)
    tokens_per_minute: int | None = Field(default=None, ge=-1)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        """Reject empty patches (no fields set) and reject ``0`` caps."""
        explicit = self.model_dump(exclude_unset=True)
        if not explicit:
            msg = (
                "rate-limit patch must set at least one field; an empty "
                "patch has no effect"
            )
            raise ValueError(msg)
        # Reject ``0`` explicitly; only ``-1`` (unlimited sentinel) and
        # positive ints are meaningful caps.  ``ge=-1`` accepts 0 via
        # Pydantic but the wire contract is ``positive | -1 | unset``.
        for field, value in explicit.items():
            if value == 0:
                msg = (
                    f"{field}: cap must be a positive int or -1 (unlimited); "
                    "use null/unset to leave unchanged"
                )
                raise ValueError(msg)
        return self


# ── Preset override ───────────────────────────────────────────────────


class PresetOverride(BaseModel):
    """Persisted operator override on top of an in-code preset.

    Overrides are merged into the in-code preset at read time by
    :class:`PresetOverrideService`.  ``None`` fields mean "inherit
    from base preset"; non-``None`` fields replace the preset's
    corresponding field.

    Cloud-only and local-only presets enforce field shape:
    ``candidate_urls`` is illegal on cloud presets (cloud endpoints
    are statically known and never probed) and ``base_url`` is illegal
    on local presets (local presets carry candidates instead).
    Cross-shape validation lives in the service layer because
    ``PresetOverride`` is also persisted with ``preset_name`` as the
    primary key and the catalog of preset shapes is loaded lazily.

    Attributes:
        preset_name: Name of the underlying preset.
        default_models: Override for ``CloudPreset.default_models`` /
            ``LocalPreset.default_models``.
        supported_auth_types: Override for the preset's allowed auth
            types.
        candidate_urls: Override for ``LocalPreset.candidate_urls``;
            illegal on cloud presets.
        base_url: Override for ``CloudPreset.base_url``; illegal on
            local presets.
        updated_at: UTC timestamp of the last override write.
        updated_by: Actor id of the last override writer.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    preset_name: NotBlankStr = Field(description="Preset this override targets")
    default_models: tuple[ProviderModelConfig, ...] | None = Field(
        default=None,
        description="Override for default model list",
    )
    supported_auth_types: tuple[AuthType, ...] | None = Field(
        default=None,
        description="Override for allowed auth types",
    )
    candidate_urls: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Override for local-preset candidate URLs",
    )
    base_url: NotBlankStr | None = Field(
        default=None,
        description="Override for cloud-preset base URL",
    )
    updated_at: AwareDatetime | None = Field(
        default=None,
        description="UTC timestamp of last override write",
    )
    updated_by: NotBlankStr | None = Field(
        default=None,
        description="Actor id of last override writer",
    )


class PresetOverrideUpdateRequest(BaseModel):
    """Partial-update payload for ``PATCH /providers/presets/{name}``.

    Every override field is optional.  ``None`` means "clear the
    override and inherit from the base preset"; omitted means "leave
    unchanged".  Use ``model_dump(exclude_unset=True)`` to distinguish.

    Attributes:
        default_models: New default model list, or ``None`` to clear.
        supported_auth_types: New allowed auth types, or ``None`` to clear.
        candidate_urls: New candidate URLs, or ``None`` to clear.
        base_url: New base URL, or ``None`` to clear.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    default_models: tuple[ProviderModelConfig, ...] | None = None
    supported_auth_types: tuple[AuthType, ...] | None = None
    candidate_urls: tuple[NotBlankStr, ...] | None = None
    base_url: NotBlankStr | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        """Reject empty patches; an empty patch has no effect."""
        explicit = self.model_dump(exclude_unset=True)
        if not explicit:
            msg = (
                "preset-override patch must set at least one field; an "
                "empty patch has no effect"
            )
            raise ValueError(msg)
        return self


# ── Credentials rotation ──────────────────────────────────────────────


class _ApiKeyRotation(BaseModel):
    """Discriminated-union variant: rotate an API-key provider."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    auth_type: Literal[AuthType.API_KEY]
    api_key: SecretStr = Field(min_length=8)


class _SubscriptionRotation(BaseModel):
    """Discriminated-union variant: rotate a subscription-token provider."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    auth_type: Literal[AuthType.SUBSCRIPTION]
    subscription_token: SecretStr = Field(min_length=8)
    tos_accepted: bool = Field(description="ToS re-acceptance is required on rotate")


class _CustomHeaderRotation(BaseModel):
    """Discriminated-union variant: rotate a custom-header provider."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    auth_type: Literal[AuthType.CUSTOM_HEADER]
    custom_header_name: NotBlankStr = Field(max_length=200)
    custom_header_value: SecretStr = Field(min_length=1)


class _OAuthRotation(BaseModel):
    """Discriminated-union variant: rotate an OAuth provider."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    auth_type: Literal[AuthType.OAUTH]
    oauth_token_url: NotBlankStr
    oauth_client_id: NotBlankStr
    oauth_client_secret: SecretStr = Field(min_length=8)
    oauth_scope: NotBlankStr | None = None


CredentialsRotateRequest = Annotated[
    _ApiKeyRotation | _SubscriptionRotation | _CustomHeaderRotation | _OAuthRotation,
    Field(discriminator="auth_type"),
]
"""Discriminated union of rotation payloads keyed by ``auth_type``.

Pydantic resolves the variant by reading ``auth_type``; the controller
validates the variant matches the persisted provider's ``auth_type``
(rotating an api_key provider with a subscription payload is rejected
at the service layer with HTTP 422).
"""


# ── Manual model add + bulk model sync ────────────────────────────────


class AddModelRequest(BaseModel):
    """Payload for ``POST /providers/{name}/models``.

    Adds a single ``ProviderModelConfig`` to the provider's persisted
    model list.  Used when the model is not in ``litellm.model_cost``
    and the operator knows the exact id + pricing -- bypasses
    discovery.  Conflict (model already exists) returns HTTP 409.

    Attributes:
        model: The model spec to add.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    model: ProviderModelConfig = Field(description="Model spec to add")


class SyncModelsRequest(BaseModel):
    """Payload for ``POST /providers/{name}/models/sync``.

    Re-runs discovery + pricing enrichment from ``litellm.model_cost``
    and replaces the persisted model list with the merged result.

    Attributes:
        replace_existing: When True (default), the persisted list is
            replaced.  When False, discovered models that already exist
            keep their existing config (pricing, alias) and only new
            ones are appended.
        preset_hint: Optional preset name passed to ``discover_models``
            for endpoint-shape selection (Ollama vs standard /models).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    replace_existing: bool = Field(
        default=True,
        description="Replace persisted list (True) vs append-only merge (False)",
    )
    preset_hint: NotBlankStr | None = Field(
        default=None,
        description="Optional preset hint for discovery shape",
    )


class SyncModelsResponse(BaseModel):
    """Result of a bulk model sync.

    Reports the diff between the previous persisted list and the new
    merged list.  All three lists are sorted by model id for stable
    rendering.

    Attributes:
        added: Model ids that appeared in the synced list and were not
            in the previous list.
        removed: Model ids that were in the previous list and are no
            longer present (only populated when ``replace_existing`` is
            True; append-only mode never removes).
        updated: Model ids whose config (pricing, capabilities, max
            context) changed.
        models: The new persisted model list.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    added: tuple[NotBlankStr, ...]
    removed: tuple[NotBlankStr, ...]
    updated: tuple[NotBlankStr, ...]
    models: tuple[ProviderModelConfig, ...]


__all__ = (
    "AddModelRequest",
    "CredentialsRotateRequest",
    "PresetOverride",
    "PresetOverrideUpdateRequest",
    "ProviderAuditActor",
    "ProviderAuditEvent",
    "ProviderAuditEventType",
    "RateLimitsResponse",
    "RateLimitsUpdateRequest",
    "SyncModelsRequest",
    "SyncModelsResponse",
)
