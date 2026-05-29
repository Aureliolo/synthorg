"""Authentication domain models."""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.auth.roles import HumanRole
from synthorg.core.types import NotBlankStr


class AuthMethod(StrEnum):
    """Authentication method used for a request."""

    JWT = "jwt"
    API_KEY = "api_key"
    WS_TICKET = "ws_ticket"


class OrgRole(StrEnum):
    """Permission-level role for org configuration access.

    Orthogonal to ``HumanRole`` (operational persona).
    ``HumanRole`` controls who you are in the org simulation;
    ``OrgRole`` controls what you can do to the org config.
    """

    OWNER = "owner"
    DEPARTMENT_ADMIN = "department_admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class User(BaseModel):
    """Persisted user account.

    Attributes:
        id: Unique user identifier.
        username: Login username.
        password_hash: Argon2id hash (excluded from repr).
        role: Access control role.
        must_change_password: Whether the user must change password.
        org_roles: Permission-level roles for org config access.
        scoped_departments: Departments accessible to dept admins.
        created_at: Account creation timestamp.
        updated_at: Last modification timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    username: NotBlankStr
    password_hash: str = Field(repr=False)
    role: HumanRole
    must_change_password: bool = True
    org_roles: tuple[OrgRole, ...] = ()
    scoped_departments: tuple[NotBlankStr, ...] = ()
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_scoped_departments(self) -> User:
        """Reject non-empty scoped_departments without DEPARTMENT_ADMIN.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``scoped_departments`` is set without the
                ``DEPARTMENT_ADMIN`` org role, or that role is present
                without any scoped departments.
        """
        if self.scoped_departments and OrgRole.DEPARTMENT_ADMIN not in self.org_roles:
            msg = "scoped_departments requires DEPARTMENT_ADMIN in org_roles"
            raise ValueError(msg)
        if OrgRole.DEPARTMENT_ADMIN in self.org_roles and not self.scoped_departments:
            msg = "DEPARTMENT_ADMIN requires non-empty scoped_departments"
            raise ValueError(msg)
        return self


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        org_roles: Permission-level roles for org config access.
        scoped_departments: Departments accessible to dept admins.
        session_id: JWT ``jti`` (or ``None`` for non-JWT methods).
            Long-lived connections (WS, SSE) consult
            ``session_store.is_revoked(session_id)`` periodically so an
            admin revocation kicks the connection out instead of
            waiting for the access token to expire.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    user_id: NotBlankStr
    username: NotBlankStr
    role: HumanRole
    auth_method: AuthMethod
    must_change_password: bool = False
    org_roles: tuple[OrgRole, ...] = ()
    scoped_departments: tuple[NotBlankStr, ...] = ()
    session_id: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_scoped_departments(self) -> AuthenticatedUser:
        """Reject non-empty scoped_departments without DEPARTMENT_ADMIN.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``scoped_departments`` is set without the
                ``DEPARTMENT_ADMIN`` org role, or that role is present
                without any scoped departments.
        """
        if self.scoped_departments and OrgRole.DEPARTMENT_ADMIN not in self.org_roles:
            msg = "scoped_departments requires DEPARTMENT_ADMIN in org_roles"
            raise ValueError(msg)
        if OrgRole.DEPARTMENT_ADMIN in self.org_roles and not self.scoped_departments:
            msg = "DEPARTMENT_ADMIN requires non-empty scoped_departments"
            raise ValueError(msg)
        return self
