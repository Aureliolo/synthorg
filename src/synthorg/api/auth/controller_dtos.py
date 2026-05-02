"""Request/response DTOs for the auth controller.

Extracted from ``controller.py`` to keep that file focused on
the Litestar route handlers.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.api.auth.config import AuthConfig
from synthorg.api.guards import HumanRole  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001

_MIN_PASSWORD_LENGTH: int = AuthConfig().min_password_length


def _check_password_length(password: str) -> str:
    """Validate that a password meets the minimum length requirement.

    Args:
        password: Password to validate.

    Returns:
        The password unchanged.

    Raises:
        ValueError: If the password is too short.
    """
    if len(password) < _MIN_PASSWORD_LENGTH:
        msg = f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"
        raise ValueError(msg)
    return password


class SetupRequest(BaseModel):
    """First-run admin account creation payload.

    Attributes:
        username: Admin login username.
        password: Admin password (minimum configured length).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    username: NotBlankStr = Field(max_length=128)
    password: NotBlankStr = Field(max_length=128)

    @model_validator(mode="after")
    def _validate_password_length(self) -> Self:
        """Reject passwords shorter than the minimum."""
        _check_password_length(self.password)
        return self


class LoginRequest(BaseModel):
    """Login credentials payload.

    Attributes:
        username: Login username.
        password: Login password.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    username: NotBlankStr = Field(max_length=128)
    password: NotBlankStr = Field(max_length=128)


class ChangePasswordRequest(BaseModel):
    """Password change payload.

    Attributes:
        current_password: Current password for verification.
        new_password: New password (minimum configured length).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    current_password: NotBlankStr = Field(max_length=128)
    new_password: NotBlankStr = Field(max_length=128)

    @model_validator(mode="after")
    def _validate_password_length(self) -> Self:
        """Reject new passwords shorter than the minimum."""
        _check_password_length(self.new_password)
        return self


class CookieSessionResponse(BaseModel):
    """Cookie-based session response.

    The JWT is delivered via an HttpOnly ``Set-Cookie`` header,
    not in the response body.  This DTO contains only the
    metadata the frontend needs.

    Attributes:
        expires_in: Session lifetime in seconds.
        must_change_password: Whether password change is required.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    expires_in: int = Field(gt=0)
    must_change_password: bool


class UserInfoResponse(BaseModel):
    """Current user information.

    Attributes:
        id: User ID.
        username: Login username.
        role: Access control role.
        must_change_password: Whether password change is required.
        org_roles: Permission-level roles for org config access.
        scoped_departments: Departments accessible to dept admins.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr
    username: NotBlankStr
    role: HumanRole
    must_change_password: bool
    org_roles: tuple[NotBlankStr, ...] = ()
    scoped_departments: tuple[NotBlankStr, ...] = ()


class WsTicketResponse(BaseModel):
    """One-time WebSocket connection ticket.

    Attributes:
        ticket: Single-use, short-lived ticket string.
        expires_in: Ticket lifetime in seconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    ticket: NotBlankStr
    expires_in: int = Field(gt=0)


class SessionResponse(BaseModel):
    """Active JWT session response DTO.

    Attributes:
        session_id: Unique session identifier (JWT ``jti``).
        user_id: Session owner's user ID.
        username: Session owner's login name.
        ip_address: Client IP at login time.
        user_agent: Client User-Agent at login time.
        created_at: Session creation timestamp.
        last_active_at: Last activity timestamp.
        expires_at: Session expiry timestamp.
        is_current: Whether this is the caller's current session.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    session_id: NotBlankStr
    user_id: NotBlankStr
    username: NotBlankStr
    ip_address: str
    user_agent: str
    created_at: AwareDatetime
    last_active_at: AwareDatetime
    expires_at: AwareDatetime
    is_current: bool = False
