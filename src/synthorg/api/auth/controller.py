"""Authentication controller — setup, login, password change, me."""

import uuid
from datetime import UTC, datetime
from typing import Any, Self

from litestar import Controller, Request, Response, get, post
from litestar.connection import ASGIConnection  # noqa: TC002
from litestar.exceptions import PermissionDeniedException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.models import AuthenticatedUser, User
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.api.dto import ApiResponse
from synthorg.api.errors import ConflictError, UnauthorizedError
from synthorg.api.guards import HumanRole
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_FAILED,
    API_AUTH_PASSWORD_CHANGED,
    API_AUTH_SETUP_COMPLETE,
    API_AUTH_TOKEN_ISSUED,
)

logger = get_logger(__name__)

# Derive from AuthConfig default to prevent silent divergence.
_MIN_PASSWORD_LENGTH: int = AuthConfig.model_fields["min_password_length"].default

# Pre-computed Argon2id hash for constant-time rejection when the
# username doesn't exist — prevents timing-based username enumeration.
# The actual password is irrelevant; only the verification time matters.
_DUMMY_ARGON2_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c2FsdHNhbHRzYWx0$"
    "mB0bZKSNwOhSdxMQfsldT3qGmFyjVqbkntMkutMfdUs"
)


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


# ── Request DTOs ──────────────────────────────────────────────


class SetupRequest(BaseModel):
    """First-run admin account creation payload.

    Attributes:
        username: Admin login username.
        password: Admin password (min 12 chars).
    """

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    username: NotBlankStr = Field(max_length=128)
    password: NotBlankStr = Field(max_length=128)


class ChangePasswordRequest(BaseModel):
    """Password change payload.

    Attributes:
        current_password: Current password for verification.
        new_password: New password (min 12 chars).
    """

    model_config = ConfigDict(frozen=True)

    current_password: NotBlankStr = Field(max_length=128)
    new_password: NotBlankStr = Field(max_length=128)

    @model_validator(mode="after")
    def _validate_password_length(self) -> Self:
        """Reject new passwords shorter than the minimum."""
        _check_password_length(self.new_password)
        return self


# ── Response DTOs ─────────────────────────────────────────────


class TokenResponse(BaseModel):
    """JWT token response.

    Attributes:
        token: Encoded JWT string.
        expires_in: Token lifetime in seconds.
        must_change_password: Whether password change is required.
    """

    model_config = ConfigDict(frozen=True)

    token: NotBlankStr
    expires_in: int = Field(gt=0)
    must_change_password: bool


class UserInfoResponse(BaseModel):
    """Current user information.

    Attributes:
        id: User ID.
        username: Login username.
        role: Access control role.
        must_change_password: Whether password change is required.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr
    username: NotBlankStr
    role: HumanRole
    must_change_password: bool


_PWD_CHANGE_EXEMPT_SUFFIXES = ("/auth/change-password", "/auth/me")

# ── Guards ────────────────────────────────────────────────────


def require_password_changed(
    connection: ASGIConnection,  # type: ignore[type-arg]
    _: object,
) -> None:
    """Guard that blocks users who must change their password.

    Paths ending with ``/auth/change-password`` or ``/auth/me``
    are exempt so the user can actually change the password or
    inspect their own profile.

    Args:
        connection: The incoming connection.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If password change is required
            or the user object is present but not an
            ``AuthenticatedUser``.
    """
    path = str(connection.url.path)
    if any(path.endswith(s) for s in _PWD_CHANGE_EXEMPT_SUFFIXES):
        return
    user = connection.scope.get("user")
    if user is None:
        return
    if not isinstance(user, AuthenticatedUser):
        logger.warning(
            API_AUTH_FAILED,
            reason="unexpected_user_type",
            user_type=type(user).__qualname__,
            path=path,
        )
        raise PermissionDeniedException(detail="Invalid user session")
    if user.must_change_password:
        raise PermissionDeniedException(detail="Password change required")


# ── Controller ────────────────────────────────────────────────


class AuthController(Controller):
    """Authentication endpoints: setup, login, password change, me."""

    path = "/auth"
    tags = ("auth",)

    @post(
        "/setup",
        status_code=201,
        summary="First-run admin setup",
    )
    async def setup(
        self,
        data: SetupRequest,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[TokenResponse]]:
        """Create the first admin account (CEO).

        Only available when no users exist. Returns 409 after
        the first account is created.
        """
        app_state = request.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service
        persistence = app_state.persistence

        user_count = await persistence.users.count()
        if user_count > 0:
            logger.warning(API_AUTH_FAILED, reason="setup_already_completed")
            msg = "Setup already completed"
            raise ConflictError(msg)

        now = datetime.now(UTC)
        password_hash = await auth_service.hash_password_async(data.password)
        user = User(
            id=str(uuid.uuid4()),
            username=data.username,
            password_hash=password_hash,
            role=HumanRole.CEO,
            must_change_password=True,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(user)

        # Race guard: undo if another setup completed concurrently
        post_count = await persistence.users.count()
        if post_count > 1:
            await persistence.users.delete(user.id)
            logger.warning(API_AUTH_FAILED, reason="setup_race_detected")
            msg = "Setup already completed"
            raise ConflictError(msg)

        token, expires_in = auth_service.create_token(user)

        logger.info(
            API_AUTH_SETUP_COMPLETE,
            user_id=user.id,
            username=user.username,
        )

        return Response(
            content=ApiResponse(
                data=TokenResponse(
                    token=token,
                    expires_in=expires_in,
                    must_change_password=user.must_change_password,
                ),
            ),
            status_code=201,
        )

    @post(
        "/login",
        status_code=200,
        summary="Authenticate with credentials",
    )
    async def login(
        self,
        data: LoginRequest,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[TokenResponse]]:
        """Validate credentials and return a JWT."""
        app_state = request.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service
        persistence = app_state.persistence

        user = await persistence.users.get_by_username(data.username)
        if user is not None:
            password_valid = await auth_service.verify_password_async(
                data.password, user.password_hash
            )
        else:
            # Constant-time rejection: run verification against a
            # dummy hash to prevent timing-based username enumeration.
            await auth_service.verify_password_async(data.password, _DUMMY_ARGON2_HASH)
            password_valid = False

        if not password_valid:
            logger.warning(
                API_AUTH_FAILED,
                reason="invalid_credentials",
            )
            msg = "Invalid credentials"
            raise UnauthorizedError(msg)

        token, expires_in = auth_service.create_token(user)

        logger.info(
            API_AUTH_TOKEN_ISSUED,
            user_id=user.id,
            username=user.username,
        )

        return Response(
            content=ApiResponse(
                data=TokenResponse(
                    token=token,
                    expires_in=expires_in,
                    must_change_password=user.must_change_password,
                ),
            ),
        )

    @post(
        "/change-password",
        status_code=200,
        summary="Change current user password",
    )
    async def change_password(
        self,
        data: ChangePasswordRequest,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[UserInfoResponse]]:
        """Validate current password and set new one."""
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            msg = "Authentication required"
            raise UnauthorizedError(msg)
        app_state = request.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service
        persistence = app_state.persistence

        user = await persistence.users.get(auth_user.user_id)
        if user is None:
            logger.warning(
                API_AUTH_FAILED,
                reason="user_not_found_for_password_change",
                user_id=auth_user.user_id,
            )
            msg = "User not found"
            raise UnauthorizedError(msg)

        if not await auth_service.verify_password_async(
            data.current_password, user.password_hash
        ):
            logger.warning(
                API_AUTH_FAILED,
                reason="invalid_current_password",
                user_id=user.id,
            )
            msg = "Invalid current password"
            raise UnauthorizedError(msg)

        now = datetime.now(UTC)
        new_hash = await auth_service.hash_password_async(data.new_password)
        updated_user = user.model_copy(
            update={
                "password_hash": new_hash,
                "must_change_password": False,
                "updated_at": now,
            }
        )
        await persistence.users.save(updated_user)

        logger.info(
            API_AUTH_PASSWORD_CHANGED,
            user_id=user.id,
            username=user.username,
        )

        return Response(
            content=ApiResponse(
                data=UserInfoResponse(
                    id=updated_user.id,
                    username=updated_user.username,
                    role=updated_user.role,
                    must_change_password=False,
                ),
            ),
        )

    @get(
        "/me",
        summary="Get current user info",
    )
    async def me(
        self,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[UserInfoResponse]]:
        """Return information about the authenticated user."""
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            msg = "Authentication required"
            raise UnauthorizedError(msg)

        return Response(
            content=ApiResponse(
                data=UserInfoResponse(
                    id=auth_user.user_id,
                    username=auth_user.username,
                    role=auth_user.role,
                    must_change_password=auth_user.must_change_password,
                ),
            ),
        )
