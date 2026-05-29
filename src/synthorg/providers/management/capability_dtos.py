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

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import safe_error_description
from synthorg.providers.enums import (
    AuthType,
)


def _require_utc(value: datetime) -> datetime:
    """Reject ``AwareDatetime`` values whose offset is not exactly UTC.

    ``AwareDatetime`` accepts any non-naive offset (``+02:00``,
    ``-07:00``, etc.), but every persisted timestamp on this surface
    is documented and stored as UTC.  Enforcing the invariant at the
    DTO boundary keeps round-trips deterministic and pushes the
    burden of normalisation off downstream layers.

    Returns:
        The unchanged *value* when its UTC offset is exactly zero.

    Raises:
        ValueError: If the datetime's offset is not exactly UTC.
    """
    if value.utcoffset() != UTC.utcoffset(None):
        msg = f"datetime must be in UTC; got offset {value.utcoffset()!r}"
        raise ValueError(msg)
    return value


UTCDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]


def _recursively_freeze(value: Any) -> Any:
    """Return an immutable equivalent of ``value`` for audit-payload safety.

    Walks the structure and produces ``MappingProxyType`` for dicts and
    ``tuple`` for lists/tuples; scalars pass through unchanged.
    ``MappingProxyType`` instances re-enter the recursion so nested
    already-frozen mappings are normalised against the outer wrap.

    Sets and frozensets are explicitly rejected: an audit row needs a
    deterministic on-disk JSON shape so callers can diff and replay it,
    and Python's set iteration order is not stable across runs.  Senders
    that need set semantics in the payload should pass a sorted tuple
    instead.

    Returns:
        An immutable equivalent of *value* (``MappingProxyType`` for
        dicts, ``tuple`` for lists/tuples; scalars unchanged).

    Raises:
        TypeError: If *value* is a ``set`` or ``frozenset`` (forbidden
            for determinism).
    """
    if isinstance(value, (set, frozenset)):
        msg = (
            f"audit payload disallows {type(value).__name__} for determinism; "
            "use a sorted tuple instead"
        )
        raise TypeError(msg)
    if isinstance(value, MappingProxyType):
        # Re-freeze recursively so nested values inserted prior to
        # wrapping still get the same treatment.
        return MappingProxyType(
            {k: _recursively_freeze(v) for k, v in value.items()},
        )
    if isinstance(value, dict):
        return MappingProxyType(
            {k: _recursively_freeze(v) for k, v in value.items()},
        )
    if isinstance(value, (list, tuple)):
        return tuple(_recursively_freeze(item) for item in value)
    return value


def _recursively_thaw(value: Any) -> Any:
    """Inverse of :func:`_recursively_freeze` for JSON serialisation.

    Pydantic-core / msgspec cannot encode ``MappingProxyType`` directly,
    so each immutable container is converted back to its mutable
    JSON-friendly counterpart (``dict`` / ``list``).  Tuples become
    lists for the same reason.  Sets / frozensets are rejected by
    :func:`_recursively_freeze` so the inverse never sees them either,
    but keep the explicit rejection here so a caller that bypasses
    ``_freeze_payload`` (e.g. by mutating the model after construction)
    fails fast at serialise time rather than emitting a non-deterministic
    audit row.

    Returns:
        A JSON-serialisable copy of *value* with ``MappingProxyType``
        replaced by ``dict`` and tuples replaced by ``list``.

    Raises:
        TypeError: If *value* is a ``set`` or ``frozenset`` (forbidden
            for determinism).
    """
    if isinstance(value, (set, frozenset)):
        msg = (
            f"audit payload disallows {type(value).__name__} for determinism; "
            "use a sorted tuple instead"
        )
        raise TypeError(msg)
    if isinstance(value, MappingProxyType):
        return {k: _recursively_thaw(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {k: _recursively_thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_recursively_thaw(item) for item in value]
    return value


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    row is read-only forever and any plaintext leak is permanent;
    use ``safe_error_description`` (or an equivalent redactor) for
    any operator-readable detail.

    Attributes:
        id: Monotonic row identifier assigned by persistence on save.
        provider_name: The provider the mutation targeted.
        event_type: Mutation category (see :data:`ProviderAuditEventType`).
        actor: Who performed the mutation.
        payload: Event-specific metadata, JSON-serialisable.
        occurred_at: UTC timestamp of the mutation.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        # Allow ``mappingproxy`` and frozen mappings on the ``payload``
        # field after the validator below converts the input dict into
        # a :class:`MappingProxyType`.
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    id: int | None = Field(default=None, ge=1, description="Repo-assigned row id")
    provider_name: NotBlankStr = Field(description="Provider name the mutation targets")
    event_type: ProviderAuditEventType = Field(description="Mutation category")
    actor: ProviderAuditActor = Field(description="Actor performing the mutation")
    payload: Mapping[str, Any] = Field(
        default_factory=dict,
        description=(
            "Event-specific metadata; credentials must be masked. "
            "Wrapped in MappingProxyType after validation so the audit "
            "row stays append-only at the Python level too."
        ),
    )
    occurred_at: UTCDatetime = Field(description="UTC timestamp of the mutation")

    @model_validator(mode="after")
    def _freeze_payload(self) -> Self:
        """Recursively freeze ``payload`` so the audit row is fully immutable.

        ``frozen=True`` on the model only prevents attribute
        reassignment; without this hook the audit row's ``payload``
        dict could be mutated post-construction, breaking the
        append-only contract this model documents.  A shallow
        ``MappingProxyType`` wrap is insufficient -- nested ``dict`` /
        ``list`` / ``set`` values stay mutable, so a caller could
        still rewrite ``event.payload["nested"]["k"] = "evil"`` and
        violate the audit invariant.  ``_recursively_freeze`` walks
        the entire payload tree and produces an immutable equivalent
        (``MappingProxyType``, ``tuple``, ``frozenset``); the companion
        ``_serialize_payload`` field-serializer thaws back to plain
        builtins at JSON-encode time so msgspec / pydantic-core
        serialization still succeeds.

        Returns:
            The validated instance with ``payload`` replaced by a
            recursively frozen ``MappingProxyType`` tree.
        """
        frozen = _recursively_freeze(self.payload)
        # ``MappingProxyType`` is not its own type; identity check
        # against the original avoids double-freezing on re-validate.
        if frozen is not self.payload:
            object.__setattr__(self, "payload", frozen)
        return self

    @field_serializer("payload")
    def _serialize_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Recursively thaw the immutable payload back to plain builtins.

        Pydantic-core / msgspec cannot encode ``MappingProxyType``,
        ``tuple`` (when typed as ``Mapping`` value), or ``frozenset``
        directly; ``_recursively_thaw`` produces a JSON-encodable copy
        that's independent of the in-memory immutable structure.

        Returns:
            A plain ``dict`` copy of the payload with all immutable
            containers converted to ``dict``/``list`` for JSON encoding.
        """
        thawed = _recursively_thaw(payload)
        # Outer container is always a Mapping after thaw because
        # ``payload`` is typed as ``Mapping[str, Any]``.  Defensive
        # ``cast`` rather than ``assert`` so ``-O`` builds keep the
        # contract.
        return cast("dict[str, Any]", thawed)


# ── Rate-limit override ───────────────────────────────────────────────


class RateLimitsResponse(BaseModel):
    """Effective rate-limit configuration for one provider.

    Maps the persisted :class:`synthorg.core.resilience_config.RateLimiterConfig`
    to a wire shape.  Both fields use ``0`` to mean "unlimited" on the
    storage side and on the wire (matching the existing config
    semantics; the client-side rate limiter treats ``0`` as no cap).

    Attributes:
        requests_per_minute: Per-provider RPM cap (``0`` = unlimited).
        concurrent_requests: Max concurrent in-flight requests
            (``0`` = unlimited).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    requests_per_minute: int = Field(
        default=0,
        ge=0,
        description="Per-provider RPM cap; 0 = unlimited",
    )
    concurrent_requests: int = Field(
        default=0,
        ge=0,
        description="Max concurrent in-flight requests; 0 = unlimited",
    )


class RateLimitsUpdateRequest(BaseModel):
    """Partial-update payload for ``PATCH /providers/{name}/rate-limits``.

    Every field is optional; omitting a field means "leave unchanged".
    Pass ``0`` to set a cap to "unlimited" (matching the persisted
    ``RateLimiterConfig`` semantics).  Pass a positive int to apply a
    new cap.  Negative values are rejected.

    At least one field MUST be present in the body; an empty patch is
    rejected with HTTP 422 (callers should not PATCH without intent to
    change something).

    Attributes:
        requests_per_minute: New RPM cap (``0`` = unlimited), or unset
            to leave unchanged.
        concurrent_requests: New concurrent cap (``0`` = unlimited), or
            unset to leave unchanged.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    requests_per_minute: int | None = Field(default=None, ge=0)
    concurrent_requests: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        """Reject empty patches and explicit-``null`` field values.

        ``{"requests_per_minute": null}`` would otherwise satisfy the
        "set at least one field" rule via ``exclude_unset`` while
        leaving every value semantically ``None``.  The intent of the
        endpoint is to set a cap (``0`` or positive int) or leave a
        cap unchanged (omit the field); explicit ``null`` is neither
        and must surface as an explicit validation failure.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If no fields were set, or any explicitly-provided
                field has a ``null`` value.
        """
        explicit = self.model_dump(exclude_unset=True)
        if not explicit:
            msg = (
                "rate-limit patch must set at least one field; an empty "
                "patch has no effect"
            )
            raise ValueError(msg)
        explicit_nones = [k for k, v in explicit.items() if v is None]
        if explicit_nones:
            msg = (
                f"rate-limit patch fields must be a non-negative integer "
                f"(use 0 for 'unlimited'); explicit null is rejected for "
                f"{sorted(explicit_nones)!r}"
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    updated_at: UTCDatetime | None = Field(
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

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    default_models: tuple[ProviderModelConfig, ...] | None = None
    supported_auth_types: tuple[AuthType, ...] | None = None
    candidate_urls: tuple[NotBlankStr, ...] | None = None
    base_url: NotBlankStr | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        """Reject empty patches; an empty patch has no effect.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If no fields were explicitly set (empty patch).
        """
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

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    auth_type: Literal[AuthType.API_KEY]
    api_key: SecretStr = Field(min_length=8)


class _SubscriptionRotation(BaseModel):
    """Discriminated-union variant: rotate a subscription-token provider."""

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    auth_type: Literal[AuthType.SUBSCRIPTION]
    subscription_token: SecretStr = Field(min_length=8)
    tos_accepted: bool = Field(description="ToS re-acceptance is required on rotate")


class _CustomHeaderRotation(BaseModel):
    """Discriminated-union variant: rotate a custom-header provider."""

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    auth_type: Literal[AuthType.CUSTOM_HEADER]
    custom_header_name: NotBlankStr = Field(max_length=200)
    custom_header_value: SecretStr = Field(min_length=1)


class _OAuthRotation(BaseModel):
    """Discriminated-union variant: rotate an OAuth provider."""

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    auth_type: Literal[AuthType.OAUTH]
    oauth_token_url: NotBlankStr
    oauth_client_id: NotBlankStr
    oauth_client_secret: SecretStr = Field(min_length=8)
    oauth_scope: NotBlankStr | None = None

    @field_validator("oauth_token_url")
    @classmethod
    def _check_oauth_token_url(cls, v: str) -> str:
        """Reject OAuth token URLs without an http(s) scheme, host, or port.

        Returns:
            The validated ``oauth_token_url`` unchanged.

        Raises:
            ValueError: If the URL lacks an http/https scheme or a host,
                or carries a malformed port.
        """
        # Inline check (no import from .dtos to avoid the
        # providers / security / providers.cost_recording circular import
        # path).  Mirrors ``_validate_oauth_token_url`` in .dtos.
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            msg = (
                f"oauth_token_url must use http or https scheme, got {parsed.scheme!r}"
            )
            raise ValueError(msg)
        # ``parsed.hostname`` rejects host-less inputs (``http:///path``)
        # and userinfo-only forms that ``parsed.netloc`` happens to
        # accept; mirrors ``_validate_http_url`` in .dtos.
        if not parsed.hostname:
            msg = "oauth_token_url must include a host"
            raise ValueError(msg)
        try:
            _ = parsed.port  # raises on malformed ``host:bad`` ports
        except ValueError as exc:
            msg = f"oauth_token_url has malformed port: {safe_error_description(exc)}"
            raise ValueError(msg) from exc
        return v


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

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

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
            for endpoint-shape selection (self-hosted vs standard
            ``/models``).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    added: tuple[NotBlankStr, ...]
    removed: tuple[NotBlankStr, ...]
    updated: tuple[NotBlankStr, ...]
    models: tuple[ProviderModelConfig, ...]


__all__ = [
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
]
