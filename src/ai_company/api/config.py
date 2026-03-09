"""API configuration models.

Frozen Pydantic models for CORS, rate limiting, server, and the
top-level ``ApiConfig`` that aggregates them all.
"""

from pydantic import BaseModel, ConfigDict, Field


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
        default=("Content-Type", "Authorization", "X-Human-Role"),
        description="Headers permitted in cross-origin requests",
    )
    allow_credentials: bool = Field(
        default=False,
        description="Whether credentials are allowed",
    )


class RateLimitConfig(BaseModel):
    """API rate limiting configuration.

    Attributes:
        rate_limit: Rate limit rules (e.g. ``("100/minute",)``).
        exclude_paths: Paths excluded from rate limiting.
    """

    model_config = ConfigDict(frozen=True)

    rate_limit: tuple[str, ...] = Field(
        default=("100/minute",),
        description="Rate limit rules",
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


class ApiConfig(BaseModel):
    """Top-level API configuration aggregating all sub-configs.

    Attributes:
        cors: CORS configuration.
        rate_limit: Rate limiting configuration.
        server: Uvicorn server configuration.
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
    api_prefix: str = Field(
        default="/api/v1",
        description="URL prefix for all API routes",
    )
