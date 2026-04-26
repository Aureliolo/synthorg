"""Auth controller -- setup, login, password change, me, ws-ticket, sessions."""

import math
import uuid
from datetime import UTC, datetime
from typing import Any

from litestar import Controller, Request, Response, delete, get, post
from litestar.exceptions import PermissionDeniedException
from litestar.middleware.rate_limit import RateLimitConfig as LitestarRateLimitConfig

from synthorg.api.auth.controller_dtos import (
    ChangePasswordRequest,
    CookieSessionResponse,
    LoginRequest,
    SessionResponse,
    SetupRequest,
    UserInfoResponse,
    WsTicketResponse,
)
from synthorg.api.auth.controller_helpers import (
    create_session_record,
    extract_jti,
    get_auth_config,
    make_session_cookies,
)
from synthorg.api.auth.cookies import (
    make_clear_csrf_cookie,
    make_clear_refresh_cookie,
    make_clear_session_cookie,
)
from synthorg.api.auth.models import AuthenticatedUser, AuthMethod, User
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.api.auth.system_user import SYSTEM_USERNAME, is_system_user
from synthorg.api.auth.ticket_store import TicketLimitExceededError
from synthorg.api.dto import ApiResponse
from synthorg.api.errors import (
    AccountLockedError,
    ApiValidationError,
    ConflictError,
    NotFoundError,
    UnauthorizedError,
)
from synthorg.api.guards import HumanRole
from synthorg.api.rate_limits.guard import per_op_rate_limit
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_SESSION_LISTED,
    API_SESSION_REVOKE_FAILED,
)
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_AUTH_PASSWORD_CHANGED,
    SECURITY_AUTH_SETUP_COMPLETE,
    SECURITY_AUTH_TOKEN_ISSUED,
    SECURITY_SESSION_FORCE_LOGOUT,
    SECURITY_SESSION_REVOKED,
)

logger = get_logger(__name__)

# Pre-computed Argon2id hash for constant-time rejection when the
# username doesn't exist -- prevents timing-based username enumeration.
# The actual password is irrelevant; only the verification time matters.
_DUMMY_ARGON2_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c2FsdHNhbHRzYWx0$"
    "mB0bZKSNwOhSdxMQfsldT3qGmFyjVqbkntMkutMfdUs"
)


# ── Controller ────────────────────────────────────────────────


_AUTH_RATE_LIMIT = LitestarRateLimitConfig(
    rate_limit=("minute", 10),
)
"""Stricter rate limiter for auth endpoints (10 req/min).

Applied as route-level middleware on ``/auth/login``,
``/auth/setup``, and ``/auth/change-password``.  Keyed by remote
IP (``request.client.host``).  Each ``RateLimitConfig`` instance
produces a middleware with an independent store, so counters do
not collide with the global rate limiter.

.. note::

   Behind a reverse proxy (e.g. nginx in Docker), Litestar's
   default ``get_remote_address`` reads ``request.client.host``
   which is the proxy IP, not the real client.  Enable Uvicorn's
   ``--proxy-headers`` and ``--forwarded-allow-ips`` to trust
   ``X-Forwarded-For`` from the proxy.
"""


_VALID_SCOPES: frozenset[str] = frozenset({"own", "all"})


class AuthController(Controller):
    """Authentication endpoints: setup, login, password change, me, sessions, logout."""

    path = "/auth"
    tags = ("auth",)

    @post(
        "/setup",
        status_code=201,
        summary="First-run admin setup",
        middleware=[_AUTH_RATE_LIMIT.middleware],
    )
    async def setup(
        self,
        data: SetupRequest,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[CookieSessionResponse]]:
        """Create the first admin account (CEO).

        Only available when no users exist. Returns 409 after
        the first account is created.  The JWT is delivered via
        an HttpOnly ``Set-Cookie`` header.
        """
        app_state = request.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service
        persistence = app_state.persistence

        if data.username == SYSTEM_USERNAME:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="setup_reserved_username",
                username=data.username,
            )
            msg = "Username 'system' is reserved"
            raise ConflictError(msg)

        ceo_count = await persistence.users.count_by_role(HumanRole.CEO)
        if ceo_count > 0:
            logger.warning(SECURITY_AUTH_FAILED, reason="setup_already_completed")
            msg = "Setup already completed"
            raise ConflictError(msg)

        now = datetime.now(UTC)
        password_hash = await auth_service.hash_password_async(data.password)
        user = User(
            id=str(uuid.uuid4()),
            username=data.username,
            password_hash=password_hash,
            role=HumanRole.CEO,
            must_change_password=False,
            created_at=now,
            updated_at=now,
        )
        await persistence.users.save(user)

        # Race guard: undo if another setup completed concurrently
        post_ceo_count = await persistence.users.count_by_role(HumanRole.CEO)
        if post_ceo_count > 1:
            await persistence.users.delete(user.id)
            logger.warning(SECURITY_AUTH_FAILED, reason="setup_race_detected")
            msg = "Setup already completed"
            raise ConflictError(msg)

        token, expires_in, session_id = auth_service.create_token(user)

        await create_session_record(
            request,
            app_state,
            session_id,
            user,
            expires_in,
        )

        auth_config = get_auth_config(app_state)
        if app_state.has_session_store:
            await app_state.session_store.enforce_session_limit(
                user.id,
                auth_config.max_concurrent_sessions,
            )

        logger.info(
            SECURITY_AUTH_SETUP_COMPLETE,
            user_id=user.id,
            username=user.username,
        )

        return Response(
            content=ApiResponse(
                data=CookieSessionResponse(
                    expires_in=expires_in,
                    must_change_password=user.must_change_password,
                ),
            ),
            status_code=201,
            cookies=await make_session_cookies(
                token,
                expires_in,
                auth_config,
                app_state=app_state,
                session_id=session_id,
                user_id=user.id,
            ),
        )

    @post(
        "/login",
        status_code=200,
        summary="Authenticate with credentials",
        middleware=[_AUTH_RATE_LIMIT.middleware],
    )
    async def login(
        self,
        data: LoginRequest,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[CookieSessionResponse]]:
        """Validate credentials and set session cookie."""
        app_state = request.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service
        persistence = app_state.persistence

        # Account lockout check (still run dummy hash for timing safety)
        if app_state.has_lockout_store and app_state.lockout_store.is_locked(
            data.username,
        ):
            await auth_service.verify_password_async(data.password, _DUMMY_ARGON2_HASH)
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="account_locked",
                username=data.username,
            )
            raise AccountLockedError(
                retry_after=app_state.lockout_store.lockout_duration_seconds,
            )

        user = await persistence.users.get_by_username(data.username)
        if user is not None and is_system_user(user.id):
            # System user cannot log in -- run dummy hash for
            # constant-time rejection (prevent timing enumeration).
            await auth_service.verify_password_async(data.password, _DUMMY_ARGON2_HASH)
            password_valid = False
        elif user is not None:
            password_valid = await auth_service.verify_password_async(
                data.password, user.password_hash
            )
        else:
            # Constant-time rejection: run verification against a
            # dummy hash to prevent timing-based username enumeration.
            await auth_service.verify_password_async(data.password, _DUMMY_ARGON2_HASH)
            password_valid = False

        if not password_valid:
            # Record failure for lockout tracking
            if app_state.has_lockout_store:
                client = request.client
                ip = client.host if client else ""
                locked = await app_state.lockout_store.record_failure(
                    data.username, ip_address=ip
                )
                if locked:
                    raise AccountLockedError(
                        retry_after=app_state.lockout_store.lockout_duration_seconds,
                    )
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="invalid_credentials",
            )
            msg = "Invalid credentials"
            raise UnauthorizedError(msg)

        # Clear lockout on success
        if app_state.has_lockout_store:
            await app_state.lockout_store.record_success(data.username)

        token, expires_in, session_id = auth_service.create_token(user)

        await create_session_record(
            request,
            app_state,
            session_id,
            user,
            expires_in,
        )

        auth_config = get_auth_config(app_state)
        if app_state.has_session_store:
            await app_state.session_store.enforce_session_limit(
                user.id,
                auth_config.max_concurrent_sessions,
            )

        logger.info(
            SECURITY_AUTH_TOKEN_ISSUED,
            user_id=user.id,
            username=user.username,
        )

        return Response(
            content=ApiResponse(
                data=CookieSessionResponse(
                    expires_in=expires_in,
                    must_change_password=user.must_change_password,
                ),
            ),
            cookies=await make_session_cookies(
                token,
                expires_in,
                auth_config,
                app_state=app_state,
                session_id=session_id,
                user_id=user.id,
            ),
        )

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
        """Validate current password and set new one."""
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
        auth_service: AuthService = app_state.auth_service
        persistence = app_state.persistence

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

        # Revoke the old session before issuing a new one.
        old_jti = extract_jti(request)
        if old_jti and app_state.has_session_store:
            await app_state.session_store.revoke(old_jti)

        # Rotate session cookie so the new pwd_sig is embedded.
        token, expires_in, session_id = auth_service.create_token(
            updated_user,
        )
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
            per_op_rate_limit(
                "auth.ws_ticket",
                max_requests=20,
                window_seconds=60,
                key="user",
            ),
        ],
    )
    async def ws_ticket(
        self,
        request: Request[Any, Any, Any],
    ) -> Response[ApiResponse[WsTicketResponse]]:
        """Exchange a valid JWT for a short-lived, single-use WS ticket.

        Issue a short-lived, single-use ticket for WebSocket connections.
        The ticket is passed as a query parameter instead of the JWT, so
        long-lived credentials never appear in URLs or server logs.
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
        try:
            ticket = app_state.ticket_store.create(ws_user)
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
                    expires_in=max(1, math.ceil(app_state.ticket_store.ttl_seconds)),
                ),
            ),
        )

    # ── Session management ─────────────────────────────────────

    @get(
        "/sessions",
        summary="List active sessions",
    )
    async def list_sessions(
        self,
        request: Request[Any, Any, Any],
        scope: str = "own",
    ) -> Response[ApiResponse[list[SessionResponse]]]:
        """List active sessions. CEO: ``?scope=all`` for all users."""
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
            raise ApiValidationError(msg)

        app_state = request.app.state["app_state"]
        store = app_state.session_store

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
        session_id: str,
    ) -> None:
        """Revoke a session. Own sessions or CEO any."""
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="unauthenticated_session_revoke",
            )
            msg = "Authentication required"
            raise UnauthorizedError(msg)

        app_state = request.app.state["app_state"]
        store = app_state.session_store

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

    @post(
        "/logout",
        status_code=204,
        summary="Logout current session",
    )
    async def logout(
        self,
        request: Request[Any, Any, Any],
    ) -> Response[None]:
        """Revoke the current session (if any) and clear cookies.

        Idempotent: always returns 204 with cookie-clearing headers,
        whether or not the caller is authenticated.  This lets clients
        recover from stale cookie state (e.g. an app-version upgrade
        that invalidated the session semantics) without first needing
        a valid session to call logout -- which would be a catch-22.
        Revoking the server-side session record is a best-effort
        extra step when the JWT is still valid.
        """
        auth_user = request.scope.get("user")
        app_state = request.app.state["app_state"]

        # Revocation runs regardless of whether auth_middleware
        # resolved a user: ``/auth/logout`` is in the auth
        # ``exclude_paths`` (so clients can recover from stale
        # state without a valid session), which means
        # ``scope["user"]`` is typically unset here even when a
        # valid JWT cookie is presented.  Parse the JTI directly
        # from the request so the server-side session record is
        # still invalidated -- otherwise the JWT would remain
        # usable until natural expiry, which defeats the purpose
        # of logout.
        user_id = (
            auth_user.user_id if isinstance(auth_user, AuthenticatedUser) else None
        )
        jti = extract_jti(request)
        if jti and app_state.has_session_store:
            # Best-effort revocation -- if the session store is
            # unreachable we still return 204 with cleared cookies
            # so the client can recover from stale state.  Without
            # this, a transient DB error would 500 the logout and
            # trap users in the exact stale-cookie scenario the
            # idempotent contract was designed to fix.
            try:
                await app_state.session_store.revoke(jti)
                logger.info(
                    SECURITY_SESSION_FORCE_LOGOUT,
                    session_id=jti,
                    user_id=user_id,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as err:
                logger.warning(
                    API_SESSION_REVOKE_FAILED,
                    session_id=jti,
                    user_id=user_id,
                    error_type=type(err).__name__,
                    error=str(err),
                )

        auth_config = get_auth_config(
            app_state,
        )
        return Response(
            content=None,
            status_code=204,
            cookies=[
                make_clear_session_cookie(auth_config),
                make_clear_csrf_cookie(auth_config),
                make_clear_refresh_cookie(auth_config),
            ],
            headers={"Clear-Site-Data": '"cookies"'},
        )
