"""API configuration models.

Frozen Pydantic models for CORS, rate limiting, server,
authentication, and the top-level ``ApiConfig`` that aggregates
them all.
"""

import ipaddress
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.auth.config import AuthConfig
from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_NETWORK_EXPOSURE_WARNING,
)

logger = get_logger(__name__)


class CorsConfig(BaseModel):
    """CORS configuration for the API.

    Attributes:
        allowed_origins: Origins permitted to make cross-origin requests.
        allow_methods: HTTP methods permitted in cross-origin requests.
        allow_headers: Headers permitted in cross-origin requests.
        allow_credentials: Whether credentials (cookies, auth) are
            allowed in cross-origin requests.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    # Empty by default: safe-by-default for production. Local dev sets
    # the origin explicitly via the settings registry
    # (``api.cors_allowed_origins`` in
    # ``src/synthorg/settings/definitions/api.py``) or env var
    # ``SYNTHORG_API_CORS_ALLOWED_ORIGINS``. CFG-1 audit: flipped from
    # the previous Vite-dev default of ``("http://localhost:5173",)``.
    allowed_origins: tuple[str, ...] = Field(
        default=(),
        description="Origins permitted to make cross-origin requests",
    )
    allow_methods: tuple[str, ...] = Field(
        default=("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"),
        description="HTTP methods permitted in cross-origin requests",
    )
    allow_headers: tuple[str, ...] = Field(
        default=("Content-Type", "Authorization", "X-CSRF-Token"),
        description="Headers permitted in cross-origin requests",
    )
    allow_credentials: bool = Field(
        default=True,
        description="Whether credentials (cookies) are allowed",
    )

    @model_validator(mode="after")
    def _validate_wildcard_credentials(self) -> Self:
        """Reject ``*`` origin with ``allow_credentials=True``.

        Browsers reject ``Access-Control-Allow-Origin: *`` combined
        with ``Access-Control-Allow-Credentials: true``.
        """
        if self.allow_credentials and "*" in self.allowed_origins:
            msg = (
                "allow_credentials=True is incompatible with "
                "allowed_origins containing '*'"
            )
            raise ValueError(msg)
        return self


class RateLimitTimeUnit(StrEnum):
    """Valid time windows for rate limiting."""

    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"


class RateLimitConfig(BaseModel):
    """API rate limiting configuration.

    Three tiers stacked around the auth middleware:

    - **IP floor** (outermost, un-gated): keyed by client IP, applies
      to every request -- including ones the auth middleware rejects
      with 401.  Guards against flood attacks that burn auth-validation
      cycles on protected endpoints with forged tokens.
    - **Unauthenticated** (middle, only when ``scope["user"]`` is
      ``None``): keyed by client IP, aggressive cap on brute-force
      against login/setup/logout.
    - **Authenticated** (innermost, only when ``scope["user"]`` is
      set): keyed by user ID, generous cap for normal dashboard use.

    Keying authenticated limits by user ID instead of IP prevents
    multi-user deployments behind a shared gateway or NAT from
    collectively exhausting a single per-IP budget.

    Attributes:
        floor_max_requests: Maximum total requests per time window
            (by IP) across the whole API.  Catches traffic that
            auth_middleware rejects before the unauth tier sees it.
        unauth_max_requests: Maximum unauthenticated requests per
            time window (by IP).
        auth_max_requests: Maximum authenticated requests per time
            window (by user ID).
        time_unit: Time window (``second``, ``minute``, ``hour``,
            ``day``).
        exclude_paths: Paths excluded from rate limiting.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    floor_max_requests: int = Field(
        default=10000,
        ge=1,
        description=(
            "Maximum total requests per time window (by IP) across"
            " the whole API, including requests rejected by the auth"
            " middleware.  Defense-in-depth against floods of invalid"
            " auth attempts on protected endpoints.  The floor wraps"
            " both user-gated tiers in the middleware stack, so it"
            " must be >= ``auth_max_requests`` AND >="
            " ``unauth_max_requests`` -- a lower floor would silently"
            " cap either the authenticated per-user budget or the"
            " unauthenticated per-IP budget below its documented"
            " value (especially behind a shared NAT where many users"
            " share one IP).  Enforced by"
            " :meth:`_validate_floor_above_user_tiers`."
        ),
    )
    unauth_max_requests: int = Field(
        default=20,
        ge=1,
        description="Maximum unauthenticated requests per time window (by IP)",
    )
    auth_max_requests: int = Field(
        default=6000,
        ge=1,
        description="Maximum authenticated requests per time window (by user ID)",
    )
    time_unit: RateLimitTimeUnit = Field(
        default=RateLimitTimeUnit.MINUTE,
        description="Time window (second, minute, hour, day)",
    )
    exclude_paths: tuple[str, ...] = Field(
        default=("/api/v1/healthz", "/api/v1/readyz"),
        description="Paths excluded from rate limiting",
    )
    max_rpm_default: int = Field(
        default=60,
        ge=1,
        le=100_000,
        description=(
            "Fallback requests-per-minute applied to per-connection"
            " coordinators when the catalog does not provide a limiter"
            " (mirrors the api.max_rpm_default setting; restart required)"
        ),
    )

    @model_validator(mode="after")
    def _validate_floor_above_user_tiers(self) -> Self:
        """Reject a floor lower than either user-gated cap.

        The IP floor wraps both the unauthenticated and authenticated
        tiers in the middleware stack, so it is a hard ceiling on
        both.  If ``floor_max_requests`` is below either cap the
        corresponding budget can never be reached and shared-IP
        deployments (office NAT, corporate gateway) would silently
        regress to the floor cap.  Require operators to size the
        floor above both user-gated caps.
        """
        if self.floor_max_requests < self.auth_max_requests:
            msg = (
                f"floor_max_requests={self.floor_max_requests} must be"
                f" >= auth_max_requests={self.auth_max_requests} so"
                " the authenticated per-user budget is reachable"
                " (the IP floor wraps the authenticated tier)."
            )
            raise ValueError(msg)
        if self.floor_max_requests < self.unauth_max_requests:
            msg = (
                f"floor_max_requests={self.floor_max_requests} must be"
                f" >= unauth_max_requests={self.unauth_max_requests} so"
                " the unauthenticated per-IP budget is reachable"
                " (the IP floor wraps the unauthenticated tier)."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_max_requests(cls, data: Any) -> Any:
        """Reject the removed ``max_requests`` field with guidance."""
        if isinstance(data, dict) and "max_requests" in data:
            msg = (
                "'max_requests' was replaced by 'unauth_max_requests' "
                "and 'auth_max_requests' in v0.6.3"
            )
            raise ValueError(msg)
        return data


class ServerConfig(BaseModel):
    """Uvicorn server configuration.

    Attributes:
        host: Bind address.
        port: Bind port.
        reload: Enable auto-reload for development.
        workers: Number of worker processes.
        ws_ping_interval: WebSocket ping interval in seconds
            (0 to disable).
        ws_ping_timeout: WebSocket pong timeout in seconds.
        ssl_certfile: Path to SSL certificate file (PEM format).
        ssl_keyfile: Path to SSL private key file (PEM format).
        ssl_ca_certs: Path to CA bundle for client cert
            verification.
        trusted_proxies: IP addresses/CIDRs trusted as reverse
            proxies for ``X-Forwarded-For``/``X-Forwarded-Proto``
            header processing.
        compression_minimum_size_bytes: Minimum response body size
            in bytes before brotli compression kicks in. Mirrors the
            ``api.compression_minimum_size_bytes`` setting (restart
            required); the API startup hook resolves the current
            value and threads it in here so operator tuning via the
            settings database takes effect on next boot.
        request_max_body_size_bytes: Maximum accepted HTTP request
            body size in bytes. Mirrors the
            ``api.request_max_body_size_bytes`` setting (restart
            required); populated the same way as
            ``compression_minimum_size_bytes``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    host: str = Field(
        default="127.0.0.1",
        description="Bind address",
    )
    port: int = Field(
        default=3001,
        ge=1,
        le=65535,
        description="Bind port",
    )
    reload: bool = Field(
        default=False,
        description="Enable auto-reload for development",
    )
    workers: int = Field(
        default=1,
        ge=1,
        le=32,
        description="Number of worker processes",
    )
    ws_ping_interval: float = Field(
        default=20.0,
        ge=0,
        description="WebSocket ping interval in seconds (0 to disable)",
    )
    ws_ping_timeout: float = Field(
        default=20.0,
        ge=0,
        description="WebSocket pong timeout in seconds",
    )
    ssl_certfile: str | None = Field(
        default=None,
        description="Path to SSL certificate file (PEM format)",
    )
    ssl_keyfile: str | None = Field(
        default=None,
        description="Path to SSL private key file (PEM format)",
    )
    ssl_ca_certs: str | None = Field(
        default=None,
        description=("Path to CA bundle for client certificate verification"),
    )
    trusted_proxies: tuple[str, ...] = Field(
        default=(),
        description=(
            "IP addresses/CIDRs trusted as reverse proxies "
            "for X-Forwarded-For/Proto header processing"
        ),
    )
    compression_minimum_size_bytes: int = Field(
        default=1000,
        ge=100,
        le=10_000,
        description=(
            "Minimum response body size in bytes before brotli compression"
            " is applied (mirrors the api.compression_minimum_size_bytes"
            " setting; restart required)"
        ),
    )
    request_max_body_size_bytes: int = Field(
        default=52_428_800,
        ge=1_000_000,
        le=536_870_912,
        description=(
            "Maximum accepted HTTP request body size in bytes (mirrors"
            " the api.request_max_body_size_bytes setting; restart"
            " required)"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_empty_tls(cls, data: Any) -> Any:
        """Normalize empty-string TLS paths to ``None``."""
        if not isinstance(data, dict):
            return data
        overrides: dict[str, object] = {}
        for key in ("ssl_certfile", "ssl_keyfile", "ssl_ca_certs"):
            val = data.get(key)
            if isinstance(val, str) and not val.strip():
                overrides[key] = None
        if not overrides:
            return data
        return {**data, **overrides}

    @model_validator(mode="after")
    def _validate_tls_pair(self) -> Self:
        """Require both cert and key when either is set."""
        has_cert = self.ssl_certfile is not None
        has_key = self.ssl_keyfile is not None
        has_ca = self.ssl_ca_certs is not None

        if has_cert and not has_key:
            msg = "ssl_keyfile is required when ssl_certfile is set"
            raise ValueError(msg)
        if has_key and not has_cert:
            msg = "ssl_certfile is required when ssl_keyfile is set"
            raise ValueError(msg)
        if has_ca and not has_cert:
            msg = "ssl_certfile is required when ssl_ca_certs is set"
            raise ValueError(msg)

        # Validate trusted_proxies as valid IP/CIDR entries.
        for entry in self.trusted_proxies:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                msg = (
                    f"Invalid trusted_proxies entry: {entry!r} "
                    f"(must be an IP address or CIDR notation)"
                )
                raise ValueError(msg) from None
            if network.prefixlen == 0:
                msg = (
                    f"Overly broad trusted_proxies entry: {entry!r} "
                    f"trusts all addresses -- use specific IPs/CIDRs"
                )
                raise ValueError(msg)

        _wildcard_hosts = {"0.0.0.0", "::"}  # noqa: S104
        if self.host in _wildcard_hosts and not has_cert and not self.trusted_proxies:
            logger.warning(
                API_NETWORK_EXPOSURE_WARNING,
                host=self.host,
                note=(
                    "Server binds to all interfaces without TLS "
                    "or trusted proxy configuration"
                ),
            )

        return self


class ApiConfig(BaseModel):
    """Top-level API configuration aggregating all sub-configs.

    Attributes:
        cors: CORS configuration.
        rate_limit: Global three-tier rate limiting configuration
            (IP floor un-gated, unauthenticated by IP, authenticated
            by user ID).
        rate_limiter_enabled: Master kill switch for the three-tier
            global rate limiter.  Mirrors the
            ``api.rate_limiter_enabled`` registry entry
            (``read_only_post_init=True``): the boot-time resolver in
            ``api/app.py`` reads ``SYNTHORG_API_RATE_LIMITER_ENABLED``
            first and falls through to this YAML field, then the
            registry default.
        per_op_rate_limit: Per-operation throttling configuration
            (layered on top of the global three-tier limiter).
        per_op_concurrency: Per-operation inflight concurrency capping
            (layered on top of the sliding-window per-op limiter; caps
            simultaneous long-running requests per operation per subject).
        server: Uvicorn server configuration.
        auth: Authentication configuration.
        api_prefix: URL prefix for all API routes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    cors: CorsConfig = Field(
        default_factory=CorsConfig,
        description="CORS configuration",
    )
    rate_limit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description=(
            "Global three-tier rate limiting configuration: un-gated"
            " IP floor, unauthenticated by IP, authenticated by user ID"
        ),
    )
    rate_limiter_enabled: bool = Field(
        default=True,
        description=(
            "Master kill switch for the three-tier global rate limiter."
            " Mirrors the ``api.rate_limiter_enabled`` registry entry"
            " (read_only_post_init=True): the boot-time resolver in"
            " ``api/app.py`` reads SYNTHORG_API_RATE_LIMITER_ENABLED"
            " first and falls through to this YAML field, then the"
            " registry default."
        ),
    )
    per_op_rate_limit: PerOpRateLimitConfig = Field(
        default_factory=PerOpRateLimitConfig,
        description="Per-operation throttling (layered on the global limiter)",
    )
    per_op_concurrency: PerOpConcurrencyConfig = Field(
        default_factory=PerOpConcurrencyConfig,
        description=(
            "Per-operation inflight concurrency capping (layered on the"
            " sliding-window per-op limiter; caps simultaneous long-running"
            " requests per (operation, subject))"
        ),
    )
    server: ServerConfig = Field(
        default_factory=ServerConfig,
        description="Uvicorn server configuration",
    )
    auth: AuthConfig = Field(
        default_factory=AuthConfig,
        description="Authentication configuration",
    )
    api_prefix: NotBlankStr = Field(
        default="/api/v1",
        description="URL prefix for all API routes",
    )
