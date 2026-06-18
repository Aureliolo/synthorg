"""Authentication configuration."""

from typing import ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_int,
    parse_str_tuple_json,
)

MIN_SECRET_LENGTH: Final[int] = 32
MIN_SECRET_LENGTH_HS384: Final[int] = 48
MIN_SECRET_LENGTH_HS512: Final[int] = 64
# RFC 7518 wants an HMAC key at least as long as the hash output: 32 bytes
# for HS256, 48 for HS384, 64 for HS512. A key shorter than the output is
# silently truncated/weakened, so the floor is keyed to the chosen algorithm.
_MIN_SECRET_LENGTH_BY_ALG: Final[dict[str, int]] = {
    "HS256": MIN_SECRET_LENGTH,
    "HS384": MIN_SECRET_LENGTH_HS384,
    "HS512": MIN_SECRET_LENGTH_HS512,
}
DEFAULT_COOKIE_NAME = "session"
DEFAULT_CSRF_COOKIE_NAME = "csrf_token"
DEFAULT_CSRF_HEADER_NAME = "x-csrf-token"
DEFAULT_REFRESH_COOKIE_NAME = "refresh_token"
DEFAULT_REFRESH_COOKIE_PATH = "/api/v1/auth/refresh"

# Single cadence at which long-lived authenticated streams (WebSocket
# AND SSE) re-load the user record to honour role demotions, account
# deletions, and session revocations. 10 minutes bounds the
# post-revocation window without flooding the persistence backend.
# WS and SSE deliberately share one constant so the operationally
# documented revocation window is a single number, not two that can
# drift. Tests override by passing ``interval_seconds`` directly to
# ``_periodic_revalidate`` or monkey-patching this constant in the
# importing module; there is no AuthConfig field at runtime.
AUTH_REVALIDATE_INTERVAL_SECONDS: int = 10 * 60


def _require_valid_secret(secret: str, min_length: int = MIN_SECRET_LENGTH) -> None:
    """Raise ``ValueError`` if *secret* is non-empty but too short.

    Args:
        secret: JWT signing secret to validate.
        min_length: Minimum byte length required for the active algorithm.

    Raises:
        ValueError: If *secret* is non-empty and shorter than ``min_length``.
    """
    if secret and len(secret) < min_length:
        msg = f"jwt_secret must be at least {min_length} characters (got {len(secret)})"
        raise ValueError(msg)


class AuthConfig(BaseModel):
    """JWT and authentication configuration.

    The ``jwt_secret`` is resolved at application startup via a
    priority chain:

    1. ``SYNTHORG_JWT_SECRET`` environment variable (for multi-instance
       deployments sharing a common secret).
    2. Previously persisted secret in the ``settings`` table.
    3. Auto-generate a new secret and persist it for future runs.

    At construction time the secret may be empty -- it is populated
    before the first request is served.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="jwt_expiry_minutes",
            namespace=SettingNamespace.API,
            key="jwt_expiry_minutes",
            parse=parse_int,
        ),
        MirrorField(
            field="min_password_length",
            namespace=SettingNamespace.API,
            key="min_password_length",
            parse=parse_int,
        ),
        MirrorField(
            field="exclude_paths",
            namespace=SettingNamespace.API,
            key="auth_exclude_paths",
            parse=parse_str_tuple_json,
            only_if_env_set=True,
        ),
    )

    jwt_secret: str = Field(
        default="",
        repr=False,
        description=(
            "JWT signing secret (resolved at startup). "
            "Also used as the HMAC key for API key hash computation -- "
            "rotating this secret invalidates all stored API key hashes."
        ),
    )
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = Field(
        default="HS256",
        description=(
            "JWT signing algorithm (HMAC family). HS256 is the default and "
            "is correctly keyed at the enforced MIN_SECRET_LENGTH (32 bytes); "
            "HS384/HS512 are accepted but RFC 7518 wants a >=48/64-byte key "
            "for full strength, so operators selecting them should provision "
            "a correspondingly longer jwt_secret."
        ),
    )
    jwt_expiry_minutes: int = Field(
        default=60,
        ge=1,
        le=43200,
        description=(
            "Access-token lifetime in minutes (default 1h). Short by design: "
            "refresh-token rotation keeps sessions alive without long-lived "
            "bearer tokens."
        ),
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

    # Cookie settings
    cookie_name: NotBlankStr = Field(
        default=DEFAULT_COOKIE_NAME,
        description="Session cookie name",
    )
    cookie_secure: bool = Field(
        default=True,
        description="Secure flag on session cookies (HTTPS-only)",
    )
    cookie_samesite: Literal["strict", "lax", "none"] = Field(
        default="strict",
        description="SameSite attribute for session cookies",
    )
    cookie_path: NotBlankStr = Field(
        default="/api",
        description="Path scope for the session cookie (HttpOnly)",
    )
    csrf_cookie_path: NotBlankStr = Field(
        default="/",
        description=(
            "Path scope for the CSRF cookie (non-HttpOnly). Defaults to "
            "``/`` so ``document.cookie`` in JavaScript can read it from "
            "any SPA route; scoping it under ``/api`` (like the session "
            "cookie) would hide it from code running on application "
            "pages, breaking the double-submit pattern."
        ),
    )
    cookie_domain: NotBlankStr | None = Field(
        default=None,
        description="Domain for session cookies (None = current host)",
    )

    # CSRF
    csrf_cookie_name: NotBlankStr = Field(
        default=DEFAULT_CSRF_COOKIE_NAME,
        description="CSRF token cookie name (non-HttpOnly, JS-readable)",
    )
    csrf_header_name: NotBlankStr = Field(
        default=DEFAULT_CSRF_HEADER_NAME,
        description="Header name for CSRF token submission",
    )

    # Concurrent sessions
    max_concurrent_sessions: int = Field(
        default=5,
        ge=0,
        le=100,
        description="Max concurrent sessions per user (0 = unlimited)",
    )

    # Refresh tokens
    jwt_refresh_enabled: bool = Field(
        default=True,
        description="Enable refresh token rotation",
    )
    jwt_refresh_expiry_minutes: int = Field(
        default=10080,
        ge=1,
        le=43200,
        description="Refresh token lifetime in minutes (default 7 days)",
    )
    refresh_cookie_name: NotBlankStr = Field(
        default=DEFAULT_REFRESH_COOKIE_NAME,
        description="Refresh token cookie name",
    )
    refresh_cookie_path: NotBlankStr = Field(
        default=DEFAULT_REFRESH_COOKIE_PATH,
        description="Path scope for refresh token cookie (narrow)",
    )

    # Account lockout
    lockout_threshold: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Failed login attempts before account lockout",
    )
    lockout_window_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Sliding window for counting failed attempts",
    )
    lockout_duration_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Auto-unlock duration after lockout",
    )

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
    def _validate_secret_length(self) -> Self:
        """Reject non-empty secrets shorter than the minimum.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``jwt_secret`` is non-empty but shorter than
                the algorithm-specific minimum length (via
                ``_require_valid_secret``).
        """
        min_length = _MIN_SECRET_LENGTH_BY_ALG.get(
            self.jwt_algorithm, MIN_SECRET_LENGTH
        )
        _require_valid_secret(self.jwt_secret, min_length)
        return self

    @model_validator(mode="after")
    def _validate_refresh_expiry(self) -> Self:
        """Ensure refresh token outlives the access token.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If refresh tokens are enabled but
                ``jwt_refresh_expiry_minutes`` is not greater than
                ``jwt_expiry_minutes``.
        """
        if (
            self.jwt_refresh_enabled
            and self.jwt_refresh_expiry_minutes <= self.jwt_expiry_minutes
        ):
            msg = (
                "jwt_refresh_expiry_minutes must be greater than "
                "jwt_expiry_minutes when refresh tokens are enabled"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_cookie_settings(self) -> Self:
        """Reject invalid cookie configuration combinations.

        - ``SameSite=None`` requires ``Secure=True`` (browser
          requirement).
        - Cookie names must be distinct to avoid collisions.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``cookie_samesite`` is ``"none"`` without
                ``cookie_secure``, or the session/CSRF/refresh cookie
                names are not all distinct.
        """
        if self.cookie_samesite == "none" and not self.cookie_secure:
            msg = (
                "cookie_secure must be True when "
                "cookie_samesite is 'none' (browser requirement)"
            )
            raise ValueError(msg)
        names = [
            self.cookie_name,
            self.csrf_cookie_name,
            self.refresh_cookie_name,
        ]
        if len(set(names)) != len(names):
            msg = (
                "cookie_name, csrf_cookie_name, and "
                "refresh_cookie_name must all be distinct"
            )
            raise ValueError(msg)
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
