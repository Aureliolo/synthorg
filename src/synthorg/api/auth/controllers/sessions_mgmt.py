# module-kind: controller
"""Session-management endpoints: list + revoke active sessions."""

from typing import Any

from litestar import Controller, Request, Response, delete, get

from synthorg.api.api_core_state import session_store_of
from synthorg.api.auth.controller_dtos import SessionResponse
from synthorg.api.auth.controller_helpers import extract_jti
from synthorg.api.dto import ApiResponse
from synthorg.api.path_params import PathId
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import (
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_SESSION_LISTED,
    API_VALIDATION_FAILED,
)
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_SESSION_REVOKED,
)

logger = get_logger(__name__)

_VALID_SCOPES: frozenset[str] = frozenset({"own", "all"})


class AuthSessionsController(Controller):
    """Session management: list active sessions, revoke a session."""

    path = "/auth"
    tags = ("auth",)

    @get(
        "/sessions",
        summary="List active sessions",
    )
    async def list_sessions(
        self,
        request: Request[Any, Any, Any],
        scope: str = "own",
    ) -> Response[ApiResponse[list[SessionResponse]]]:
        """List active sessions. CEO: ``?scope=all`` for all users.

        Returns:
            Result matching the declared return annotation.

        Raises:
            UnauthorizedError: Raised on the corresponding failure path.
            ValidationError: Raised on the corresponding failure path.
        """
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="unauthenticated_session_list",
            )
            msg = "Authentication required"
            raise UnauthorizedError(msg)

        if scope not in _VALID_SCOPES:
            msg = f"Invalid scope: {scope!r}. Valid: own, all"
            logger.warning(
                API_VALIDATION_FAILED,
                reason="invalid_session_scope",
                scope=scope,
                valid_scopes=sorted(_VALID_SCOPES),
            )
            raise ValidationError(msg)

        app_state = request.app.state["app_state"]
        store = session_store_of(app_state)

        if scope == "all" and auth_user.role == HumanRole.CEO:
            sessions = await store.list_all()
        else:
            sessions = await store.list_by_user(
                auth_user.user_id,
            )

        current_jti = extract_jti(request)

        data = [
            SessionResponse(
                session_id=s.session_id,
                user_id=s.user_id,
                username=s.username,
                ip_address=s.ip_address,
                user_agent=s.user_agent,
                created_at=s.created_at,
                last_active_at=s.last_active_at,
                expires_at=s.expires_at,
                is_current=(s.session_id == current_jti),
            )
            for s in sessions
        ]

        logger.debug(
            API_SESSION_LISTED,
            user_id=auth_user.user_id,
            count=len(data),
        )

        return Response(content=ApiResponse(data=data))

    @delete(
        "/sessions/{session_id:str}",
        status_code=204,
        summary="Revoke a session",
    )
    async def revoke_session(
        self,
        request: Request[Any, Any, Any],
        session_id: PathId,
    ) -> None:
        """Revoke a session. Own sessions or CEO any.

        Raises:
            UnauthorizedError: Raised on the corresponding failure path.
            NotFoundError: Raised on the corresponding failure path.
        """
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="unauthenticated_session_revoke",
            )
            msg = "Authentication required"
            raise UnauthorizedError(msg)

        app_state = request.app.state["app_state"]
        store = session_store_of(app_state)

        session = await store.get(session_id)
        if session is None:
            msg = "Session not found"
            raise NotFoundError(msg)
        # Return 404 for not-owned (prevents session ID enum).
        if session.user_id != auth_user.user_id and auth_user.role != HumanRole.CEO:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="session_not_owned",
                session_id=session_id[:8],
                user_id=auth_user.user_id,
            )
            msg = "Session not found"
            raise NotFoundError(msg)

        revoked = await store.revoke(session_id)
        if revoked:
            logger.info(
                SECURITY_SESSION_REVOKED,
                session_id=session_id,
                revoked_by=auth_user.user_id,
            )
