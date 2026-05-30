# module-kind: controller
"""Password-change endpoint (``POST /auth/change-password``)."""

from datetime import UTC, datetime
from typing import Any

from litestar import Controller, Request, Response, post
from litestar.exceptions import PermissionDeniedException

from synthorg.api.api_core_state import (
    ApiCoreStateSlice,
    auth_service_of,
    session_store_of,
)
from synthorg.api.auth.controller_dtos import (
    ChangePasswordRequest,
    UserInfoResponse,
)
from synthorg.api.auth.controller_helpers import (
    create_session_record,
    extract_jti,
    get_auth_config,
    make_session_cookies,
)
from synthorg.api.auth.controllers._shared import _AUTH_RATE_LIMIT
from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import is_system_user
from synthorg.api.dto import ApiResponse
from synthorg.api.state import AppState
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import UnauthorizedError
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_AUTH_PASSWORD_CHANGED,
    SECURITY_SESSION_REVOKED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


async def _verify_current_password(
    auth_service: AuthService,
    persistence: Any,
    auth_user: AuthenticatedUser,
    data: ChangePasswordRequest,
) -> Any:
    """Load the user and verify the supplied current password.

    Args:
        auth_service: Auth service for password verification.
        persistence: Persistence backend (source of the user record).
        auth_user: The authenticated user changing their password.
        data: The change-password request body.

    Returns:
        The loaded ``User`` record when the current password is valid.

    Raises:
        UnauthorizedError: If the user no longer exists or the supplied
            current password does not match.
    """
    user = await persistence.users.get(auth_user.user_id)
    if user is None:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="user_not_found_for_password_change",
            user_id=auth_user.user_id,
        )
        msg = "User not found"
        raise UnauthorizedError(msg)

    if not await auth_service.verify_password_async(
        data.current_password, user.password_hash
    ):
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="invalid_current_password",
            user_id=user.id,
        )
        msg = "Invalid current password"
        raise UnauthorizedError(msg)
    return user


async def _revoke_old_session(
    app_state: AppState,
    request: Request[Any, Any, Any],
    updated_user: Any,
) -> None:
    """Revoke the caller's current session before a new one is issued.

    No-op when the request carries no JWT id or the session store is unwired
    (JWT-only deploy).

    Args:
        app_state: Application state (source of the session store).
        request: The incoming request (carries the current session JWT).
        updated_user: The user whose session is being rotated.
    """
    old_jti = extract_jti(request)
    if old_jti and app_state.slice(ApiCoreStateSlice).session_store is not None:
        revoked = await session_store_of(app_state).revoke(old_jti)
        if revoked:
            logger.info(
                SECURITY_SESSION_REVOKED,
                session_id=old_jti,
                user_id=updated_user.id,
                reason="password_change_rotation",
            )


async def _rotate_session_and_build_response(
    app_state: AppState,
    request: Request[Any, Any, Any],
    auth_service: AuthService,
    updated_user: Any,
) -> Response[ApiResponse[UserInfoResponse]]:
    """Mint a fresh session for the rotated password and build the response.

    The new token embeds the updated ``pwd_sig`` so the previous token can no
    longer authenticate after the password change.

    Args:
        app_state: Application state.
        request: The incoming request.
        auth_service: Auth service for token creation.
        updated_user: The user with the freshly-rotated password.

    Returns:
        The ``UserInfoResponse`` envelope with refreshed session cookies.
    """
    token, expires_in, session_id = auth_service.create_token(updated_user)
    await create_session_record(
        request,
        app_state,
        session_id,
        updated_user,
        expires_in,
    )
    auth_config = get_auth_config(app_state)

    logger.info(
        SECURITY_AUTH_PASSWORD_CHANGED,
        user_id=updated_user.id,
        username=updated_user.username,
    )

    return Response(
        content=ApiResponse(
            data=UserInfoResponse(
                id=updated_user.id,
                username=updated_user.username,
                role=updated_user.role,
                must_change_password=False,
                org_roles=tuple(r.value for r in updated_user.org_roles),
                scoped_departments=updated_user.scoped_departments,
            ),
        ),
        cookies=await make_session_cookies(
            token,
            expires_in,
            auth_config,
            app_state=app_state,
            session_id=session_id,
            user_id=updated_user.id,
        ),
    )


class AuthCredentialsController(Controller):
    """Credential-management endpoint: change password."""

    path = "/auth"
    tags = ("auth",)

    @post(
        "/change-password",
        status_code=200,
        summary="Change current user password",
        middleware=[_AUTH_RATE_LIMIT.middleware],
    )
    async def change_password(
        self,
        data: ChangePasswordRequest,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[UserInfoResponse]]:
        """Validate current password and set new one.

        Returns:
            ``Response[ApiResponse[UserInfoResponse]]`` instance.

        Raises:
            UnauthorizedError: Raised on the corresponding failure path.
            PermissionDeniedException: Raised on the corresponding failure path.
        """
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="change_password_auth_required",
                path=str(request.url.path),
            )
            msg = "Authentication required"
            raise UnauthorizedError(msg)
        if is_system_user(auth_user.user_id):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="system_user_modification_blocked",
                user_id=auth_user.user_id,
            )
            raise PermissionDeniedException(
                detail="System user cannot be modified",
            )
        app_state = request.app.state["app_state"]
        auth_service: AuthService = auth_service_of(app_state)
        persistence = persistence_of(app_state)

        user = await _verify_current_password(
            auth_service, persistence, auth_user, data
        )

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

        await _revoke_old_session(app_state, request, updated_user)
        return await _rotate_session_and_build_response(
            app_state, request, auth_service, updated_user
        )
