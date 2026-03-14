"""Authentication configuration."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_SECRET_LENGTH = 32


def _require_valid_secret(secret: str) -> None:
    """Raise ``ValueError`` if *secret* is non-empty but too short.

    Args:
        secret: JWT signing secret to validate.

    Raises:
        ValueError: If *secret* is non-empty and shorter than
            ``MIN_SECRET_LENGTH``.
    """
    if secret and len(secret) < MIN_SECRET_LENGTH:
        msg = (
            f"jwt_secret must be at least {MIN_SECRET_LENGTH} "
            f"characters (got {len(secret)})"
        )
        raise ValueError(msg)


class AuthConfig(BaseModel):
    """JWT and authentication configuration.

    The ``jwt_secret`` is resolved at application startup via a
    priority chain:

    1. ``SYNTHORG_JWT_SECRET`` environment variable (for multi-instance
       deployments sharing a common secret).
    2. Previously persisted secret in the ``settings`` table.
    3. Auto-generate a new secret and persist it for future runs.

    At construction time the secret may be empty — it is populated
    before the first request is served.

    Attributes:
        jwt_secret: HMAC signing key for JWT tokens and API key
            hashing (resolved at startup, repr-hidden).  Rotating
            this invalidates all stored API key hashes.
        jwt_algorithm: JWT signing algorithm (HMAC family only).
        jwt_expiry_minutes: Token lifetime in minutes.
        min_password_length: Minimum password length for setup/change.
        exclude_paths: URL paths excluded from auth middleware.
    """

    model_config = ConfigDict(frozen=True)

    jwt_secret: str = Field(
        default="",
        repr=False,
        description=(
            "JWT signing secret (resolved at startup). "
            "Also used as the HMAC key for API key hash computation — "
            "rotating this secret invalidates all stored API key hashes."
        ),
    )
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = Field(
        default="HS256",
        description="JWT signing algorithm (HMAC family)",
    )
    jwt_expiry_minutes: int = Field(
        default=1440,
        ge=1,
        le=43200,
        description="Token lifetime in minutes (default 24h)",
    )
    min_password_length: int = Field(
        default=12,
        ge=8,
        le=128,
        description="Minimum password length for setup and password change",
    )
    exclude_paths: tuple[str, ...] | None = Field(
        default=None,
        description=(
            "Regex patterns for paths excluded from authentication. "
            "When None (default), paths are auto-derived from the "
            "API prefix (health, auth/setup, auth/login, docs, "
            "scalar UI). "
            "Use ^ to anchor at the start of the path and add $ when "
            "an exact match (rather than a prefix match) is required."
        ),
    )

    @model_validator(mode="after")
    def _validate_secret_length(self) -> Self:
        """Reject non-empty secrets shorter than the minimum."""
        _require_valid_secret(self.jwt_secret)
        return self

    def with_secret(self, secret: str) -> AuthConfig:
        """Return a copy with the JWT secret set.

        Args:
            secret: Resolved JWT signing secret.

        Returns:
            New ``AuthConfig`` with the secret populated.

        Raises:
            ValueError: If the secret is too short.
        """
        _require_valid_secret(secret)
        return self.model_copy(update={"jwt_secret": secret})
