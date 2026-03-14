"""Authentication domain models."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.api.guards import HumanRole  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class AuthMethod(StrEnum):
    """Authentication method used for a request."""

    JWT = "jwt"
    API_KEY = "api_key"


class User(BaseModel):
    """Persisted user account.

    Attributes:
        id: Unique user identifier (UUID).
        username: Login username.
        password_hash: Argon2id hash (excluded from repr).
        role: Access control role.
        must_change_password: Whether the user must change password.
        created_at: Account creation timestamp.
        updated_at: Last modification timestamp.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr
    username: NotBlankStr
    password_hash: str = Field(repr=False)
    role: HumanRole
    must_change_password: bool = True
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ApiKey(BaseModel):
    """Persisted API key (hash-only storage).

    Attributes:
        id: Unique key identifier (UUID).
        key_hash: HMAC-SHA256 hex digest of the raw key.
        name: Human-readable label.
        role: Access control role.
        user_id: Owner user ID.
        created_at: Key creation timestamp (timezone-aware).
        expires_at: Optional expiry timestamp (timezone-aware).
        revoked: Whether the key has been revoked.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr
    key_hash: NotBlankStr = Field(repr=False)
    name: NotBlankStr
    role: HumanRole
    user_id: NotBlankStr
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    revoked: bool = False


class AuthenticatedUser(BaseModel):
    """Lightweight identity attached to ``connection.user``.

    Populated by the auth middleware after successful authentication.

    Attributes:
        user_id: User's unique identifier.
        username: User's login name.
        role: Access control role.
        auth_method: How the user authenticated.
        must_change_password: Whether forced password change is pending.
    """

    model_config = ConfigDict(frozen=True)

    user_id: NotBlankStr
    username: NotBlankStr
    role: HumanRole
    auth_method: AuthMethod
    must_change_password: bool = False
