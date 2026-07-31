"""Core domain models for the connection catalog.

All models are frozen Pydantic v2 ``BaseModel`` instances.  Secrets
are never stored inline -- ``SecretRef`` is an opaque handle resolved
at runtime via the configured ``SecretBackend``.
"""

import copy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.env_var_safety import validate_credential_env_var_name
from synthorg.core.npm_version import is_exact_npm_version
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.repo_scope import validate_repo_scope_entry

# Canonical database dialect set. Lives here (a leaf import for the whole
# connections package) so both the database authenticator and the catalog
# entry validate against one source rather than drifting copies.
VALID_DIALECTS: frozenset[str] = frozenset({"postgres", "mysql", "sqlite", "mariadb"})

# Per-connection webhook-receipt retention window in days. Tri-state:
#   None    -- fall back to the global
#              ``integrations.webhook_receipt_retention_days`` setting
#   0       -- never sweep this connection's receipts (opt-out)
#   N > 0   -- retain receipts up to N days, sweep older
# The constraint is enforced via Pydantic ``Field(ge=0)`` at the
# ``Connection.webhook_receipt_retention_days`` site below.
WebhookRetentionDays = int | None


class ConnectionType(StrEnum):
    """Supported external service connection types."""

    GITHUB = "github"
    GITLAB = "gitlab"
    GITEA = "gitea"
    FORGEJO = "forgejo"
    SLACK = "slack"
    SMTP = "smtp"
    DATABASE = "database"
    GENERIC_HTTP = "generic_http"
    OAUTH_APP = "oauth_app"
    A2A_PEER = "a2a_peer"
    # Backs an LLM provider's API-key credential (minted on provider create).
    # Unlike GENERIC_HTTP it does not require a base_url: providers that route
    # through litellm's default endpoints have no base_url of their own.
    LLM_PROVIDER = "llm_provider"
    # Backs a tunnel provider's auth token (minted from the dashboard tunnel
    # card). No base_url: the tunnel target is the local API itself.
    TUNNEL = "tunnel"
    # A hosting platform a synthetic org releases a product to. The record's
    # ``metadata`` carries the platform preset and the target environment;
    # the environment is read from here and never from an agent argument,
    # so naming a production target cannot dodge production gating.
    DEPLOY = "deploy"
    # A container image registry a synthetic org publishes an image to. The
    # record's ``metadata`` carries the registry provider preset, the bound
    # repository, the release channel, and the default publish method. The
    # channel is read from here and never from an agent argument, so naming a
    # production registry target cannot dodge production gating.
    REGISTRY = "registry"


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    secret_id: NotBlankStr
    backend: NotBlankStr
    key_version: int = Field(default=1, ge=1)


class WebhookIngestState(StrEnum):
    """Whether inbound webhook deliveries to a connection can be authenticated.

    Deliberately separate from :class:`ConnectionStatus`, which reports the
    outbound probe. A signing secret is optional by design (a connection used
    only outbound never needs one), so an absent secret must not read as an
    outbound outage; equally, when the secret *is* the thing standing between a
    sender and ingest, every delivery 401s and nothing but a server log says so.
    """

    NOT_APPLICABLE = "not_applicable"
    READY = "ready"
    UNCONFIGURED = "unconfigured"


class ConnectionHealth(BaseModel):
    """Last-known health snapshot for a connection.

    Groups the two runtime health-observation fields so the cohesive
    sub-concept travels together. ``health_check_enabled`` stays a
    top-level ``Connection`` field because it is configuration (whether
    to probe), not an observation.

    The snapshot carries the whole verdict, not just its headline. A reader
    serving this instead of running a fresh probe (the aggregate-health
    endpoint does, so opening the Connections page does not re-probe every
    connection) would otherwise hand back a thinner answer than a live check:
    a failure with no reason, and a webhook state of "claims nothing" for a
    connection that in fact has an inbound path.

    Attributes:
        status: Last-known health status.
        last_check_at: Timestamp of the most recent check.
        detail: Human-readable reason the last check reported, when it had
            one. ``None`` for a clean result.
        latency_ms: Round-trip time the last check measured, when it got far
            enough to measure one.
        webhook_ingest: Whether inbound deliveries could be authenticated at
            the time of the last check.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: ConnectionStatus = ConnectionStatus.UNKNOWN
    last_check_at: AwareDatetime | None = None
    detail: str | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)
    webhook_ingest: WebhookIngestState = WebhookIngestState.NOT_APPLICABLE


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
        health: Last-known health snapshot (status + check timestamp).
        metadata: User-provided tags and notes.
        webhook_receipt_retention_days: Per-connection override for the
            webhook-receipt retention window (days). ``None`` falls back
            to ``integrations.webhook_receipt_retention_days``; ``0``
            disables sweeping for this connection's receipts.
        sensitive: Marks the connection as sensitive so the governed
            external-access tool routes every call against it (read or
            write) to human approval, not just write methods.
        allowed_repos: Least-privilege repository scope for a forge
            connection (``owner/repo`` entries, ``owner/*`` globs
            permitted). An empty tuple denies every repository
            (fail-closed): an operator selects the in-scope repositories
            for the connection before an agent can act through it.
        created_at: Creation timestamp.
        updated_at: Last modification timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: NotBlankStr
    connection_type: ConnectionType
    auth_method: AuthMethod
    base_url: NotBlankStr | None = None
    secret_refs: tuple[SecretRef, ...] = Field(default=(), exclude=True)
    rate_limiter: RateLimiterConfig | None = None
    health_check_enabled: bool = True
    health: ConnectionHealth = Field(default_factory=ConnectionHealth)
    sensitive: bool = False
    allowed_repos: tuple[NotBlankStr, ...] = ()
    metadata: dict[str, str] = Field(default_factory=dict)
    webhook_receipt_retention_days: WebhookRetentionDays = Field(
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
        """Deep-copy mutable metadata dict at construction.

        Returns:
            The instance with ``metadata`` replaced by a deep copy.
        """
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self

    @model_validator(mode="after")
    def _validate_allowed_repos(self) -> Self:
        """Reject an over-broad or malformed repo-scope entry at the source.

        Returns:
            The unchanged instance when every scope entry is well-formed.
        """
        for entry in self.allowed_repos:
            validate_repo_scope_entry(str(entry))
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
        nonce: OIDC nonce. Generated at flow start, sent in the
            authorization request, and matched against the
            ``id_token`` ``nonce`` claim on callback (OIDC ID-token
            binding). ``None`` for plain-OAuth2 connections that have
            no ``jwks_uri`` configured.
        consumed_at: When the callback exchanged this state for tokens.
            ``None`` while the flow is in flight; set when the
            callback handler successfully exchanged the code so that
            redelivered callbacks return the original connection
            name without re-exchanging.
        connection_name_returned: Connection name the original
            successful callback returned. Pairs with ``consumed_at``;
            both must be set together.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    state_token: NotBlankStr
    connection_name: NotBlankStr
    pkce_verifier: NotBlankStr | None = None
    nonce: NotBlankStr | None = None
    scopes_requested: str = ""
    redirect_uri: str = ""
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    expires_at: AwareDatetime
    consumed_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "When the callback exchanged this state for tokens. Paired "
            "with ``connection_name_returned``; both fields must be "
            "``None`` (flow in flight) or both set (post-callback) -- "
            "enforced by ``_validate_consumed_pair``."
        ),
    )
    connection_name_returned: NotBlankStr | None = Field(
        default=None,
        description=(
            "Connection name the original successful callback returned. "
            "Paired with ``consumed_at``; both fields must be ``None`` "
            "or both set."
        ),
    )

    @model_validator(mode="after")
    def _validate_expiry(self) -> Self:
        """Ensure ``expires_at`` is strictly after ``created_at``.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``expires_at`` is not strictly after
                ``created_at``.
        """
        if self.expires_at <= self.created_at:
            msg = "OAuthState.expires_at must be after created_at"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_consumed_pair(self) -> Self:
        """``consumed_at`` and ``connection_name_returned`` must move together.

        A consumed state without a returned connection name (or vice
        versa) cannot satisfy the idempotent-replay contract; the
        callback handler stamps both atomically via
        :meth:`OAuthStateRepository.mark_consumed`.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If exactly one of ``consumed_at`` /
                ``connection_name_returned`` is ``None`` while the other
                is set.
        """
        if (self.consumed_at is None) != (self.connection_name_returned is None):
            msg = (
                "OAuthState: consumed_at and connection_name_returned "
                "must be set together"
            )
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
        id_token: Raw OIDC ID token (compact JWS), when the provider
            is an OIDC IdP. Transient and sensitive (carries identity
            claims); used only to verify the ``nonce`` binding on
            callback, never persisted.
        issued_at: When the tokens were issued.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
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
    id_token: str | None = Field(default=None, repr=False, exclude=True)
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    connection_name: NotBlankStr
    event_type: NotBlankStr
    status: NotBlankStr = "received"
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
        webhook_ingest: Whether inbound deliveries can be authenticated.
            ``NOT_APPLICABLE`` when this connection has no inbound path at all,
            which is also what an unresolved check reports: the state is derived
            from configuration, so a check that never got that far claims
            nothing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    connection_name: NotBlankStr
    status: ConnectionStatus
    latency_ms: float | None = Field(default=None, ge=0.0)
    error_detail: str | None = None
    checked_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    consecutive_failures: int = Field(default=0, ge=0)
    webhook_ingest: WebhookIngestState = WebhookIngestState.NOT_APPLICABLE


class CatalogEntry(BaseModel):
    """A curated MCP server entry in the bundled catalog.

    Attributes:
        id: Unique entry identifier.
        name: Human-readable server name.
        description: What the server does.
        npm_package: NPM package name for installation.
        npm_version: Exact published version the launcher pins (``npx`` runs
            ``<npm_package>@<npm_version>``), so a reconnect can never pull a
            newly-published (potentially compromised) ``latest``.
        required_connection_type: Connection type needed (nullable).
        transport: MCP transport type (stdio or streamable_http).
        capabilities: List of capability tags.
        tags: Searchable tags.
        credential_env_map: Map of bound-connection credential field name to
            the environment variable the spawned server reads it from.
            Credentials are forwarded only by environment variable so a secret
            value can never appear in the spawned process argv.
        required_dialect: For a database-typed entry, the connection dialect
            it requires (e.g. ``"postgres"``/``"sqlite"``), since several
            entries share ``ConnectionType.DATABASE``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    name: NotBlankStr
    description: str = ""
    npm_package: NotBlankStr | None = None
    npm_version: NotBlankStr | None = None
    required_connection_type: ConnectionType | None = None
    transport: Literal["stdio", "streamable_http"] = "stdio"
    capabilities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    credential_env_map: dict[str, str] = Field(default_factory=dict)
    required_dialect: str | None = None

    @model_validator(mode="after")
    def _validate_required_dialect(self) -> Self:
        """Confine ``required_dialect`` to a known-dialect database entry.

        The dialect only disambiguates entries sharing
        ``ConnectionType.DATABASE``, so declaring one on a non-database entry
        makes it permanently uninstallable (no connection would ever carry a
        matching dialect). A misspelled dialect on a database entry is the same
        trap. Reject both at construction rather than silently ship an
        uninstallable entry.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If ``required_dialect`` is set on a non-database entry
                or is not a known dialect.
        """
        if self.required_dialect is None:
            return self
        if self.required_connection_type is not ConnectionType.DATABASE:
            msg = (
                f"Catalog entry {self.id!r}: required_dialect "
                f"{self.required_dialect!r} is only valid on a database entry "
                f"(required_connection_type={self.required_connection_type})"
            )
            raise ValueError(msg)
        if self.required_dialect not in VALID_DIALECTS:
            msg = (
                f"Catalog entry {self.id!r}: required_dialect "
                f"{self.required_dialect!r} is not one of {sorted(VALID_DIALECTS)}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_credential_env_var_names(self) -> Self:
        """Screen credential target env-var names for injection safety.

        The map's values are the env-var names a connection secret is injected
        under at spawn, so a hostile or careless entry could aim a credential at
        a loader/process-control variable (``LD_PRELOAD``, ``NODE_OPTIONS``,
        ``PATH``) and steer the child process. Reject those and any malformed
        name at construction.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If a target env-var name is malformed or dangerous.
        """
        for env_var in self.credential_env_map.values():
            validate_credential_env_var_name(env_var)
        return self

    @model_validator(mode="after")
    def _validate_credential_binding_is_stdio(self) -> Self:
        """Confine credential injection to the stdio transport.

        A materialised ``MCPServerConfig`` only injects
        ``credential_env_map`` on the stdio connect path, so an entry that
        pairs a credential map with ``streamable_http`` would be browsable but
        never installable (the config it produces is rejected). Reject that
        combination here so the catalog cannot advertise a dead entry.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If a credential map is declared on a non-stdio entry.
        """
        if self.transport != "stdio" and self.credential_env_map:
            msg = (
                f"Catalog entry {self.id!r}: credential_env_map is only "
                f"supported on the stdio transport, not {self.transport!r}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_stdio_is_version_pinned(self) -> Self:
        """Require an exact pinned ``npm_version`` on every launchable stdio entry.

        An unpinned ``npx`` spec resolves ``latest`` on every reconnect, and a
        dist-tag or semver range (``^1.0.0`` / ``1.x``) still resolves to a
        mutable version, so a newly-published (potentially compromised) release
        could be pulled silently. Requiring an exact version is the
        supply-chain guard, enforced at the model so a hand-authored or
        DB-installed stdio entry cannot bypass it.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If a stdio entry names an ``npm_package`` without an
                ``npm_version``, or the version is not an exact published
                version (a dist-tag or range).
        """
        if (
            self.transport == "stdio"
            and self.npm_package is not None
            and self.npm_version is None
        ):
            msg = (
                f"Catalog entry {self.id!r}: stdio entries must pin npm_version "
                f"(npm_package {self.npm_package!r} would resolve 'latest')"
            )
            raise ValueError(msg)
        if self.npm_version is not None and not is_exact_npm_version(self.npm_version):
            msg = (
                f"Catalog entry {self.id!r}: npm_version {self.npm_version!r} must "
                f"be an exact published version (e.g. '1.2.3'), not a dist-tag or "
                f"range"
            )
            raise ValueError(msg)
        return self
