"""API configuration models.

Frozen Pydantic models for CORS, rate limiting, server,
authentication, and the top-level ``ApiConfig`` that aggregates
them all.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.auth.config import AuthConfig
from synthorg.core.types import NotBlankStr  # noqa: TC001


class CorsConfig(BaseModel):
    """CORS configuration for the API.

    Attributes:
        allowed_origins: Origins permitted to make cross-origin requests.
        allow_methods: HTTP methods permitted in cross-origin requests.
        allow_headers: Headers permitted in cross-origin requests.
        allow_credentials: Whether credentials (cookies, auth) are
            allowed in cross-origin requests.
    """

    model_config = ConfigDict(frozen=True)

    allowed_origins: tuple[str, ...] = Field(
        default=("http://localhost:5173",),
        description="Origins permitted to make cross-origin requests",
    )
    allow_methods: tuple[str, ...] = Field(
        default=("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"),
        description="HTTP methods permitted in cross-origin requests",
    )
    allow_headers: tuple[str, ...] = Field(
        default=("Content-Type", "Authorization"),
        description="Headers permitted in cross-origin requests",
    )
    allow_credentials: bool = Field(
        default=False,
        description="Whether credentials are allowed",
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

    Maps to Litestar's built-in ``RateLimitConfig`` middleware.

    Attributes:
        max_requests: Maximum requests per time window.
        time_unit: Time window (``second``, ``minute``, ``hour``,
            ``day``).
        exclude_paths: Paths excluded from rate limiting.
    """

    model_config = ConfigDict(frozen=True)

    max_requests: int = Field(
        default=100,
        ge=1,
        description="Maximum requests per time window",
    )
    time_unit: RateLimitTimeUnit = Field(
        default=RateLimitTimeUnit.MINUTE,
        description="Time window (second, minute, hour, day)",
    )
    exclude_paths: tuple[str, ...] = Field(
        default=("/api/v1/health",),
        description="Paths excluded from rate limiting",
    )


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
    """

    model_config = ConfigDict(frozen=True)

    host: str = Field(
        default="127.0.0.1",
        description="Bind address",
    )
    port: int = Field(
        default=8000,
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


class ApiConfig(BaseModel):
    """Top-level API configuration aggregating all sub-configs.

    Attributes:
        cors: CORS configuration.
        rate_limit: Rate limiting configuration.
        server: Uvicorn server configuration.
        auth: Authentication configuration.
        api_prefix: URL prefix for all API routes.
    """

    model_config = ConfigDict(frozen=True)

    cors: CorsConfig = Field(
        default_factory=CorsConfig,
        description="CORS configuration",
    )
    rate_limit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description="Rate limiting configuration",
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
