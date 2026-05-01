"""Core domain models for the connection catalog.

All models are frozen Pydantic v2 ``BaseModel`` instances.  Secrets
are never stored inline -- ``SecretRef`` is an opaque handle resolved
at runtime via the configured ``SecretBackend``.
"""

import copy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.resilience_config import RateLimiterConfig  # noqa: TC001
from synthorg.core.types import NotBlankStr


class ConnectionType(StrEnum):
    """Supported external service connection types."""

    GITHUB = "github"
    SLACK = "slack"
    SMTP = "smtp"
    DATABASE = "database"
    GENERIC_HTTP = "generic_http"
    OAUTH_APP = "oauth_app"
    A2A_PEER = "a2a_peer"


class AuthMethod(StrEnum):
    """How credentials are provisioned for a connection."""

    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    BASIC_AUTH = "basic_auth"
    BEARER_TOKEN = "bearer_token"  # noqa: S105
    CUSTOM = "custom"


class ConnectionStatus(StrEnum):
    """Last-known health status of a connection."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class SecretRef(BaseModel):
    """Opaque reference to an encrypted secret in a ``SecretBackend``.

    Attributes:
        secret_id: Unique identifier for the secret.
        backend: Backend name that holds this secret.
        key_version: Encryption key version used.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    secret_id: NotBlankStr
    backend: NotBlankStr
    key_version: int = Field(default=1, ge=1)


class Connection(BaseModel):
    """A configured external service connection.

    Attributes:
        id: Unique identifier (UUID).
        name: User-chosen unique name.
        connection_type: Service type discriminator.
        auth_method: How credentials are provided.
        base_url: Base URL for HTTP-based services.
        secret_refs: Tuple of opaque secret references.
        rate_limiter: Optional per-connection rate limit config.
        health_check_enabled: Whether background probes run.
        health_status: Last-known health status.
        last_health_check_at: Timestamp of most recent check.
        metadata: User-provided tags and notes.
        webhook_receipt_retention_days: Per-connection override for the
            webhook-receipt retention window (days). ``None`` falls back
            to ``integrations.webhook_receipt_retention_days``; ``0``
            disables sweeping for this connection's receipts.
        created_at: Creation timestamp.
        updated_at: Last modification timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
    )
    name: NotBlankStr
    connection_type: ConnectionType
    auth_method: AuthMethod
    base_url: NotBlankStr | None = None
    secret_refs: tuple[SecretRef, ...] = ()
    rate_limiter: RateLimiterConfig | None = None
    health_check_enabled: bool = True
    health_status: ConnectionStatus = ConnectionStatus.UNKNOWN
    last_health_check_at: AwareDatetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    webhook_receipt_retention_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-connection override for webhook-receipt retention "
            "(days). None = use the global default; 0 = never sweep "
            "this connection's receipts."
        ),
    )
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    updated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy mutable metadata dict at construction."""
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self


class OAuthState(BaseModel):
    """Transient OAuth authorization state stored during a flow.

    Attributes:
        state_token: CSRF protection token.
        connection_name: Connection this flow belongs to.
        pkce_verifier: PKCE code verifier (if PKCE is used).
        scopes_requested: Space-separated scopes.
        redirect_uri: Redirect URI used for this flow.
        created_at: When the state was created.
        expires_at: When the state expires.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    state_token: NotBlankStr
    connection_name: NotBlankStr
    pkce_verifier: NotBlankStr | None = None
    scopes_requested: str = ""
    redirect_uri: str = ""
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_expiry(self) -> Self:
        """Ensure ``expires_at`` is strictly after ``created_at``."""
        if self.expires_at <= self.created_at:
            msg = "OAuthState.expires_at must be after created_at"
            raise ValueError(msg)
        return self


class OAuthToken(BaseModel):
    """OAuth token set returned by an OAuth flow.

    The ``access_token`` and ``refresh_token`` fields carry raw
    secret values -- they are *transient*, must NOT be serialized
    to logs or persisted directly, and are expected to be stored
    via the connection catalog which writes them through the
    configured secret backend. The ``*_ref`` fields are the
    opaque handles returned after the catalog stores the tokens.

    Flows return ``OAuthToken`` with raw ``access_token`` /
    ``refresh_token`` populated and ``*_ref`` set to ``None``; the
    callback handler (or token manager) then calls
    ``ConnectionCatalog.store_oauth_tokens`` which persists the
    secrets and updates the connection with real ``SecretRef``s.

    Attributes:
        access_token: Raw access token (transient).
        refresh_token: Raw refresh token (transient, optional).
        access_token_ref: SecretRef after persistence.
        refresh_token_ref: SecretRef after persistence.
        token_type: Token type (usually "Bearer").
        expires_at: When the access token expires.
        scope_granted: Space-separated scopes actually granted.
        issued_at: When the tokens were issued.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        # Raw tokens are sensitive -- exclude from repr to keep them
        # out of accidental logging and exception tracebacks.
    )

    access_token: str | None = Field(default=None, repr=False, exclude=True)
    refresh_token: str | None = Field(default=None, repr=False, exclude=True)
    access_token_ref: SecretRef | None = None
    refresh_token_ref: SecretRef | None = None
    token_type: str = "Bearer"  # noqa: S105
    expires_at: AwareDatetime | None = None
    scope_granted: str = ""
    issued_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )


class WebhookReceipt(BaseModel):
    """Log entry for a received webhook event.

    Attributes:
        id: Unique receipt identifier.
        connection_name: Source connection.
        event_type: Provider-specific event type.
        status: Processing status.
        received_at: When the webhook was received.
        processed_at: When processing completed.
        payload_json: Raw payload as JSON string.
        error: Error message if processing failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: NotBlankStr(str(uuid4())),
    )
    connection_name: NotBlankStr
    event_type: str = ""
    status: str = "received"
    received_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    processed_at: AwareDatetime | None = None
    payload_json: str = ""
    error: str | None = None


class HealthReport(BaseModel):
    """Result of a single connection health check.

    Attributes:
        connection_name: Which connection was checked.
        status: Health status outcome.
        latency_ms: Round-trip time in milliseconds.
        error_detail: Human-readable error if unhealthy.
        checked_at: When the check ran.
        consecutive_failures: Running failure count.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    connection_name: NotBlankStr
    status: ConnectionStatus
    latency_ms: float | None = Field(default=None, ge=0.0)
    error_detail: str | None = None
    checked_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    consecutive_failures: int = Field(default=0, ge=0)


class CatalogEntry(BaseModel):
    """A curated MCP server entry in the bundled catalog.

    Attributes:
        id: Unique entry identifier.
        name: Human-readable server name.
        description: What the server does.
        npm_package: NPM package name for installation.
        required_connection_type: Connection type needed (nullable).
        transport: MCP transport type (stdio or streamable_http).
        capabilities: List of capability tags.
        tags: Searchable tags.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr
    name: NotBlankStr
    description: str = ""
    npm_package: NotBlankStr | None = None
    required_connection_type: ConnectionType | None = None
    transport: Literal["stdio", "streamable_http"] = "stdio"
    capabilities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
