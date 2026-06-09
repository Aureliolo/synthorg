# module-kind: controller
"""Current-user identity endpoints: me + WebSocket ticket."""

import math

from litestar import Controller, Request, Response, get, post
from litestar.datastructures import State
from litestar.exceptions import PermissionDeniedException

from synthorg.api.api_core_state import ticket_store_of
from synthorg.api.auth.controller_dtos import (
    UserInfoResponse,
    WsTicketResponse,
)
from synthorg.api.auth.system_user import is_system_user
from synthorg.api.auth.ticket_store import TicketLimitExceededError
from synthorg.api.dto import ApiResponse
from synthorg.api.rate_limits.policies import per_op_rate_limit_from_policy
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.domain_errors import ConflictError, UnauthorizedError
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_AUTH_FAILED

logger = get_logger(__name__)


class AuthIdentityController(Controller):
    """Current-user identity: ``/me`` and WebSocket ticket issuance."""

    path = "/auth"
    tags = ("auth",)

    @get(
        "/me",
        summary="Get current user info",
    )
    async def me(
        self,
        request: Request[object, object, State],
    ) -> Response[ApiResponse[UserInfoResponse]]:
        """Return information about the authenticated user.

        Returns:
            ``Response[ApiResponse[UserInfoResponse]]`` instance.

        Raises:
            UnauthorizedError: Raised on the corresponding failure path.
        """
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="me_auth_required",
                path=str(request.url.path),
            )
            msg = "Authentication required"
            raise UnauthorizedError(msg)

        return Response(
            content=ApiResponse(
                data=UserInfoResponse(
                    id=auth_user.user_id,
                    username=auth_user.username,
                    role=auth_user.role,
                    must_change_password=auth_user.must_change_password,
                    org_roles=tuple(r.value for r in auth_user.org_roles),
                    scoped_departments=auth_user.scoped_departments,
                ),
            ),
        )

    @post(
        "/ws-ticket",
        status_code=200,
        summary="Issue a one-time WebSocket connection ticket",
        guards=[
            per_op_rate_limit_from_policy("auth.ws_ticket", key="user"),
        ],
    )
    async def ws_ticket(
        self,
        request: Request[object, object, State],
    ) -> Response[ApiResponse[WsTicketResponse]]:
        """Exchange a valid JWT for a short-lived, single-use WS ticket.

        Issue a short-lived, single-use ticket for WebSocket connections.
        The ticket is passed as a query parameter instead of the JWT, so
        long-lived credentials never appear in URLs or server logs.

        Returns:
            ``Response[ApiResponse[WsTicketResponse]]`` instance.

        Raises:
            UnauthorizedError: Raised on the corresponding failure path.
            PermissionDeniedException: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
        """
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="ws_ticket_auth_required",
                path=str(request.url.path),
            )
            msg = "Authentication required"
            raise UnauthorizedError(msg)

        if is_system_user(auth_user.user_id):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="system_user_ws_ticket_blocked",
                user_id=auth_user.user_id,
            )
            raise PermissionDeniedException(
                detail="System user cannot request WebSocket tickets",
            )

        if auth_user.auth_method != AuthMethod.JWT:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="ws_ticket_requires_jwt",
                auth_method=auth_user.auth_method.value,
                user_id=auth_user.user_id,
            )
            msg = "WebSocket tickets require JWT authentication"
            raise UnauthorizedError(msg)

        app_state = request.app.state["app_state"]
        ws_user = auth_user.model_copy(
            update={"auth_method": AuthMethod.WS_TICKET},
        )
        ticket_store = ticket_store_of(app_state)
        try:
            ticket = ticket_store.create(ws_user)
        except TicketLimitExceededError:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="ws_ticket_limit_exceeded",
                user_id=auth_user.user_id,
            )
            msg = "Too many pending tickets -- wait for existing tickets to expire"
            raise ConflictError(msg)  # noqa: B904

        return Response(
            content=ApiResponse(
                data=WsTicketResponse(
                    ticket=ticket,
                    expires_in=max(1, math.ceil(ticket_store.ttl_seconds)),
                ),
            ),
        )
