"""API configuration models.

Frozen Pydantic models for CORS, rate limiting, server,
authentication, and the top-level ``ApiConfig`` that aggregates
them all.
"""

from enum import StrEnum
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.core.auth.config import AuthConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_int,
    parse_str_tuple_json,
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="unauth_max_requests",
            namespace=SettingNamespace.API,
            key="rate_limit_unauth_max_requests",
            parse=parse_int,
        ),
        MirrorField(
            field="auth_max_requests",
            namespace=SettingNamespace.API,
            key="rate_limit_auth_max_requests",
            parse=parse_int,
        ),
        MirrorField(
            field="time_unit",
            namespace=SettingNamespace.API,
            key="rate_limit_time_unit",
        ),
        MirrorField(
            field="exclude_paths",
            namespace=SettingNamespace.API,
            key="rate_limit_exclude_paths",
            parse=parse_str_tuple_json,
        ),
        MirrorField(
            field="max_rpm_default",
            namespace=SettingNamespace.API,
            key="max_rpm_default",
            parse=parse_int,
        ),
    )

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

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
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
    def _apply_mirrors(cls, data: object) -> object:
        """Apply the mirrors.

        Returns:
            ``object`` instance.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


class ServerConfig(BaseModel):
    """Uvicorn server configuration.

    Host, port, TLS paths, trusted-proxy list, and the compression /
    request-size limits are resolved at boot via
    :func:`synthorg.settings.bootstrap_resolver.resolve_init_value`
    against the ``api.*`` registry entries rather than carried on this
    model. Only the worker-process / auto-reload / WebSocket-ping knobs
    that uvicorn needs at construction time live here.

    Attributes:
        reload: Enable auto-reload for development.
        workers: Number of worker processes.
        ws_ping_interval: WebSocket ping interval in seconds
            (0 to disable).
        ws_ping_timeout: WebSocket pong timeout in seconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
            and falls through to the registered default (env > code
            default per the Cat-2 precedence model).
        per_op_rate_limit: Per-operation throttling configuration
            (layered on top of the global three-tier limiter).
        per_op_concurrency: Per-operation inflight concurrency capping
            (layered on top of the sliding-window per-op limiter; caps
            simultaneous long-running requests per operation per subject).
        server: Uvicorn server configuration.
        auth: Authentication configuration.
        api_prefix: URL prefix for all API routes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="api_prefix",
            namespace=SettingNamespace.API,
            key="api_prefix",
        ),
        MirrorField(
            field="rate_limiter_enabled",
            namespace=SettingNamespace.API,
            key="rate_limiter_enabled",
            parse=parse_bool,
        ),
    )

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
            " and falls through to the registered default (env > code"
            " default per the Cat-2 precedence model)."
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
    readiness_probe_timeout_seconds: float = Field(
        default=4.0,
        gt=0.0,
        description=(
            "Ceiling for the /readyz dependency-probe fan-out. A hung"
            " probe returns an unavailable (503) verdict within this"
            " budget instead of stalling the probe; kept just under the"
            " typical k8s 5s readinessProbe timeout."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Apply the mirrors.

        Returns:
            ``object`` instance.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)
