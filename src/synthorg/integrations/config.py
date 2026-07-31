"""Configuration models for the integrations subsystem.

All models are frozen Pydantic ``BaseModel`` instances following
the codebase convention of ``ConfigDict(frozen=True, allow_inf_nan=False)``.
"""

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_int,
)


class ConnectionsConfig(BaseModel):
    """Connection catalog configuration.

    Attributes:
        max_connections_per_type: Upper bound per connection type.
        secret_capture_ttl_seconds: Lifetime of an out-of-band secret-capture
            handle before it expires and is swept.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="secret_capture_ttl_seconds",
            namespace=SettingNamespace.INTEGRATIONS,
            key="secret_capture_ttl_seconds",
            parse=parse_int,
        ),
    )

    max_connections_per_type: int = Field(default=100, ge=1)
    # Bounds mirror the INTEGRATIONS.secret_capture_ttl_seconds setting
    # definition (30s..3600s) so the typed config and the settings registry
    # agree on the accepted range.
    secret_capture_ttl_seconds: int = Field(default=600, ge=30, le=3600)

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Populate unset mirror fields from the settings registry.

        Returns:
            The raw model input with any unset mirror fields filled in
            from their registered settings (caller-supplied keys win).
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


class EncryptedSqliteConfig(BaseModel):
    """Config for the encrypted SQLite secret backend.

    Attributes:
        master_key_env: Environment variable holding the URL-safe
            base64-encoded 32-byte Fernet key. The operator must set
            this before the backend is used; when unset the backend
            raises :class:`MasterKeyError` at construction time and
            the app auto-downgrades to ``env_var`` with an ERROR log
            (see ``resolve_secret_backend_config``). There is no
            auto-generation of key material on disk -- losing the
            key orphans all previously stored ciphertext.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    master_key_env: NotBlankStr = "SYNTHORG_MASTER_KEY"


class EncryptedPostgresConfig(BaseModel):
    """Config for the encrypted Postgres secret backend.

    Uses the same Fernet key material as ``encrypted_sqlite``; secrets
    are stored as Fernet ciphertext in the ``connection_secrets``
    Postgres table alongside the rest of the persistence data.

    Attributes:
        master_key_env: Environment variable holding the base64-encoded
            32-byte Fernet key.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    master_key_env: NotBlankStr = "SYNTHORG_MASTER_KEY"


class EnvVarConfig(BaseModel):
    """Config for the environment variable secret backend.

    Attributes:
        prefix: Environment variable prefix for secret lookups.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    prefix: NotBlankStr = "SYNTHORG_SECRET_"


class SecretBackendConfig(BaseModel):
    """Pluggable secret storage configuration.

    Attributes:
        backend_type: Which backend to use.
        encrypted_sqlite: Settings for the SQLite-backed Fernet backend.
        encrypted_postgres: Settings for the Postgres-backed Fernet backend.
        env_var: Settings for the env-var backend.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # Neutral, vendor-agnostic discriminators so the public config
    # surface does not embed specific vendor names. The factory maps
    # these to concrete adapters internally:
    #   - ``encrypted_sqlite``: Fernet ciphertext in a SQLite DB (default)
    #   - ``encrypted_postgres``: Fernet ciphertext in a Postgres table
    #   - ``env_var``: read-only environment variable backend
    #
    # ``encrypted_sqlite`` is the config default so a bare install with
    # SQLite persistence just works. ``create_app`` auto-promotes this
    # default to ``encrypted_postgres`` when the active persistence
    # backend is Postgres, so operators do not have to keep the secret
    # backend and persistence backend in manual sync.
    backend_type: Literal[
        "encrypted_sqlite",
        "encrypted_postgres",
        "env_var",
    ] = "encrypted_sqlite"
    encrypted_sqlite: EncryptedSqliteConfig = Field(
        default_factory=EncryptedSqliteConfig,
    )
    encrypted_postgres: EncryptedPostgresConfig = Field(
        default_factory=EncryptedPostgresConfig,
    )
    env_var: EnvVarConfig = Field(
        default_factory=EnvVarConfig,
    )


class OAuthConfig(BaseModel):
    """OAuth 2.1 subsystem configuration.

    Attributes:
        redirect_uri_base: Base URL for OAuth callbacks.
        state_expiry_seconds: How long OAuth state tokens live.
        pkce_required: Require PKCE for authorization code flows.
        device_flow_poll_interval_seconds: Polling interval for device flow.
        device_flow_timeout_seconds: Max wait for device flow user grant.
        auto_refresh_threshold_seconds: Refresh tokens expiring within
            this window.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="device_flow_poll_interval_seconds",
            namespace=SettingNamespace.INTEGRATIONS,
            key="oauth_device_flow_poll_interval_seconds",
            parse=parse_int,
        ),
    )

    redirect_uri_base: str = ""
    state_expiry_seconds: int = Field(default=3600, gt=0)
    pkce_required: bool = True
    device_flow_poll_interval_seconds: int = Field(default=5, gt=0)
    device_flow_timeout_seconds: int = Field(default=600, gt=0)
    auto_refresh_threshold_seconds: int = Field(default=300, gt=0)

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Populate unset mirror fields from the settings registry.

        Returns:
            The raw model input with any unset mirror fields filled in
            from their registered settings (caller-supplied keys win).
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


class WebhooksConfig(BaseModel):
    """Webhook receiver configuration.

    Attributes:
        rate_limit_rpm: Max webhook requests per minute per connection.
        replay_window_seconds: Nonce/timestamp dedup window.
        max_payload_bytes: Maximum webhook body size.
        receipt_retention_days: How long to keep webhook receipts, in days.
            ``0`` (the default) never sweeps them.

            Mirrors ``integrations.webhook_receipt_retention_days``, so the bound
            must admit every value that setting does: a lower bound above ``0``
            would reject the documented opt-out, and the mirror parsing an
            operator's ``0`` would fail config construction at boot.

            Nothing reads this attribute. The sweep resolves the setting live on
            each tick (``api/webhook_cleanup``) so an operator's change applies
            without a restart, which a value frozen at ``RootConfig``
            construction cannot do. Kept because the mirror populates it
            regardless and the bound has to stay in step; do not wire a consumer
            to it without moving the sweep off the live resolver first.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="receipt_retention_days",
            namespace=SettingNamespace.INTEGRATIONS,
            key="webhook_receipt_retention_days",
            parse=parse_int,
        ),
    )

    rate_limit_rpm: int = Field(default=100, ge=0)
    replay_window_seconds: int = Field(default=300, gt=0)
    max_payload_bytes: int = Field(default=1_000_000, gt=0)
    # No `verify_signatures` toggle: `verify_signature` runs unconditionally on
    # every delivery. A knob nothing read looked like a supported control an
    # operator could turn off, and wiring one later would be the signature
    # bypass this path exists to prevent.
    receipt_retention_days: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Populate unset mirror fields from the settings registry.

        Returns:
            The raw model input with any unset mirror fields filled in
            from their registered settings (caller-supplied keys win).
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


class IntegrationHealthConfig(BaseModel):
    """Health monitoring configuration.

    Attributes:
        check_interval_seconds: How often the loop wakes to look for work.
            Not how often any one connection is probed -- that is decided
            per connection by its last outcome, below.
        healthy_recheck_seconds: How long a healthy connection is trusted
            before it is probed again. Long on purpose: a probe against a
            metered third-party API is not free, and re-proving a working
            connection every few minutes buys nothing.
        degraded_recheck_seconds: Recheck interval once a connection has
            started failing but has not been written off.
        unhealthy_recheck_seconds: Recheck interval for a failed
            connection, where the operator is waiting to see it recover.
        unhealthy_threshold: Consecutive failures before ``unhealthy``.
        degraded_threshold: Consecutive failures before ``degraded``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="healthy_recheck_seconds",
            namespace=SettingNamespace.INTEGRATIONS,
            key="health_healthy_recheck_seconds",
            parse=parse_int,
        ),
        MirrorField(
            field="degraded_recheck_seconds",
            namespace=SettingNamespace.INTEGRATIONS,
            key="health_degraded_recheck_seconds",
            parse=parse_int,
        ),
        MirrorField(
            field="unhealthy_recheck_seconds",
            namespace=SettingNamespace.INTEGRATIONS,
            key="health_unhealthy_recheck_seconds",
            parse=parse_int,
        ),
    )

    check_interval_seconds: int = Field(default=300, gt=0)
    # Bounds mirror the INTEGRATIONS.health_*_recheck_seconds setting
    # definitions so the typed config and the settings registry agree on the
    # accepted range.
    healthy_recheck_seconds: int = Field(default=21_600, ge=60, le=604_800)
    degraded_recheck_seconds: int = Field(default=1_800, ge=30, le=604_800)
    unhealthy_recheck_seconds: int = Field(default=300, ge=30, le=604_800)
    unhealthy_threshold: int = Field(default=3, ge=1)
    degraded_threshold: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Populate unset mirror fields from the settings registry.

        Returns:
            The raw model input with any unset mirror fields filled in
            from their registered settings (caller-supplied keys win).
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> Self:
        """Ensure ``degraded_threshold`` is not above ``unhealthy_threshold``.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``degraded_threshold`` exceeds
                ``unhealthy_threshold``.
        """
        if self.degraded_threshold > self.unhealthy_threshold:
            msg = (
                "IntegrationHealthConfig.degraded_threshold "
                f"({self.degraded_threshold}) must be <= "
                f"unhealthy_threshold ({self.unhealthy_threshold})"
            )
            raise ValueError(msg)
        return self


class TunnelConfig(BaseModel):
    """Tunnel configuration for local webhook development.

    The active provider is the live ``integrations.tunnel_provider``
    setting (DB > env > default), not static config; this model holds
    only the bootstrap knobs that must exist before settings do.

    Attributes:
        auth_token_env: Env var holding the ngrok auth token (headless
            fallback; the dashboard-managed catalog credential wins).
        cloudflared_download_enabled: Whether a missing ``cloudflared``
            binary may be downloaded from the official Cloudflare
            GitHub release at first start. Disable to require an
            operator-installed binary on PATH.
        devtunnel_download_enabled: Whether a missing ``devtunnel``
            binary may be downloaded from Microsoft's fixed asset URLs
            at first use. Disable to require an operator-installed
            binary on PATH.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    auth_token_env: NotBlankStr = "NGROK_AUTHTOKEN"  # noqa: S105
    cloudflared_download_enabled: bool = True
    devtunnel_download_enabled: bool = True


class McpCatalogConfig(BaseModel):
    """Bundled MCP server catalog configuration.

    Attributes:
        enabled: Whether the catalog is available.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True


class IntegrationsConfig(BaseModel):
    """Root integrations subsystem configuration.

    Attributes:
        enabled: Master switch for the integrations layer.
        connections: Connection catalog settings.
        secret_backend: Secret storage backend settings.
        oauth: OAuth 2.1 flow settings.
        webhooks: Webhook receiver settings.
        health: Connection health monitoring settings.
        tunnel: Local-dev tunnel settings.
        mcp_catalog: Bundled MCP server catalog settings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    connections: ConnectionsConfig = Field(
        default_factory=ConnectionsConfig,
    )
    secret_backend: SecretBackendConfig = Field(
        default_factory=SecretBackendConfig,
    )
    oauth: OAuthConfig = Field(
        default_factory=OAuthConfig,
    )
    webhooks: WebhooksConfig = Field(
        default_factory=WebhooksConfig,
    )
    health: IntegrationHealthConfig = Field(
        default_factory=IntegrationHealthConfig,
    )
    tunnel: TunnelConfig = Field(
        default_factory=TunnelConfig,
    )
    mcp_catalog: McpCatalogConfig = Field(
        default_factory=McpCatalogConfig,
    )
