"""A2A external gateway configuration."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

A2AAuthScheme = Literal[
    "api_key",
    "oauth2",
    "bearer",
    "mtls",
    "none",
]
"""Supported A2A authentication schemes."""

A2ASignatureAlgorithm = Literal["hmac-sha256"]
"""Supported push notification signature algorithms."""


class A2AAuthConfig(BaseModel):
    """Authentication configuration for A2A inbound/outbound.

    Credentials themselves are stored in the connection catalog
    via ``a2a_peer`` connections.  This config controls *which*
    scheme is used by default.

    Attributes:
        inbound_scheme: Default auth scheme for inbound requests.
        outbound_scheme: Default auth scheme for outbound requests.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    inbound_scheme: A2AAuthScheme = Field(
        default="api_key",
        description=(
            "Default inbound auth scheme (api_key, oauth2, bearer, mtls, none)"
        ),
    )
    outbound_scheme: A2AAuthScheme = Field(
        default="bearer",
        description=(
            "Default outbound auth scheme (api_key, oauth2, bearer, mtls, none)"
        ),
    )


class A2APushConfig(BaseModel):
    """Push notification configuration.

    Push events arrive at the unified webhook receiver
    (``/api/v1/webhooks/{connection_name}/a2a_push``).

    Attributes:
        enabled: Whether push notifications are accepted.
        signature_algorithm: HMAC algorithm for push signature
            verification.
        clock_skew_seconds: Maximum clock skew tolerance for
            timestamp validation.
        replay_window_seconds: Deduplication window for replay
            protection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    signature_algorithm: A2ASignatureAlgorithm = Field(
        default="hmac-sha256",
        description="HMAC algorithm for push signature verification",
    )
    clock_skew_seconds: int = Field(
        default=300,
        ge=0,
        description="Maximum clock skew tolerance in seconds",
    )
    replay_window_seconds: int = Field(
        default=60,
        ge=0,
        description="Deduplication window for replay protection",
    )


class A2AAgentCardVerificationConfig(BaseModel):
    """Agent Card signature verification configuration.

    Attributes:
        enabled: Whether to verify Agent Card signatures.
        require_signatures: Reject unsigned cards when enabled.
        trusted_jwks_urls: JWKS endpoints for signature key
            discovery.
        trusted_public_keys: PEM-encoded public keys for direct
            verification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    require_signatures: bool = False
    trusted_jwks_urls: tuple[NotBlankStr, ...] = ()
    trusted_public_keys: tuple[NotBlankStr, ...] = ()

    @model_validator(mode="after")
    def _validate_trust_sources(self) -> Self:
        """Ensure at least one trust source when signatures required."""
        if (
            self.require_signatures
            and not self.trusted_jwks_urls
            and not self.trusted_public_keys
        ):
            msg = (
                "require_signatures is True but no trust sources "
                "provided (trusted_jwks_urls and trusted_public_keys "
                "are both empty)"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="A2AAgentCardVerificationConfig",
                reason=msg,
            )
            raise ValueError(msg)
        return self


class A2AConfig(BaseModel):
    """Top-level A2A external gateway configuration.

    The gateway is disabled by default (``enabled = False``).
    When disabled, no routes are mounted and zero overhead is
    incurred.

    Attributes:
        enabled: Master switch for the A2A gateway.
        allowed_peers: Explicit peer allowlist.  Inbound requests
            from peers not in this list are rejected.  Empty tuple
            means no peers are allowed (even when enabled).
        rate_limit_per_peer_rpm: Maximum requests per minute per
            a2a_peer connection.
        sse_idle_timeout_seconds: SSE stream idle timeout before
            automatic disconnect.
        max_request_body_bytes: Maximum inbound JSON-RPC request
            body size.
        agent_card_cache_ttl_seconds: TTL for cached Agent Cards
            (0 disables caching).
        auth: Authentication scheme configuration.
        push: Push notification configuration.
        agent_card_verification: Agent Card signature verification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    allowed_peers: tuple[NotBlankStr, ...] = ()
    rate_limit_per_peer_rpm: int = Field(
        default=100,
        ge=1,
        description="Maximum requests per minute per a2a_peer",
    )
    sse_idle_timeout_seconds: int = Field(
        default=300,
        ge=30,
        description="SSE stream idle timeout in seconds",
    )
    max_request_body_bytes: int = Field(
        default=1_048_576,
        ge=1024,
        description="Maximum inbound request body size (bytes)",
    )
    agent_card_cache_ttl_seconds: int = Field(
        default=60,
        ge=0,
        description="Agent Card cache TTL (0 = no caching)",
    )
    auth: A2AAuthConfig = Field(
        default_factory=A2AAuthConfig,
        description="Authentication scheme configuration",
    )
    push: A2APushConfig = Field(
        default_factory=A2APushConfig,
        description="Push notification configuration",
    )
    agent_card_verification: A2AAgentCardVerificationConfig = Field(
        default_factory=A2AAgentCardVerificationConfig,
        description="Agent Card signature verification",
    )

    @model_validator(mode="after")
    def _validate_enabled_has_peers(self) -> Self:
        """Warn when gateway is enabled but no peers are allowed."""
        if self.enabled and not self.allowed_peers:
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="A2AConfig",
                reason=(
                    "A2A gateway is enabled but allowed_peers is "
                    "empty -- no peers will be accepted"
                ),
            )
        return self
