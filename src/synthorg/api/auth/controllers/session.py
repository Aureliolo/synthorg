# module-kind: controller
"""Session lifecycle endpoints: login, refresh, logout."""

from litestar import Controller, Request, Response, post
from litestar.datastructures import State

from synthorg.api.api_core_state import (
    ApiCoreStateSlice,
    auth_service_of,
    refresh_store_of,
    session_store_of,
)
from synthorg.api.auth.controller_dtos import (
    CookieSessionResponse,
    LoginRequest,
)
from synthorg.api.auth.controller_helpers import (
    create_session_record,
    extract_jti,
    get_auth_config,
    make_session_cookies,
)
from synthorg.api.auth.controllers._shared import (
    _AUTH_RATE_LIMIT,
    _DUMMY_ARGON2_HASH,
    _record_failed_login,
    _record_successful_login,
)
from synthorg.api.auth.cookies import (
    make_clear_csrf_cookie,
    make_clear_refresh_cookie,
    make_clear_session_cookie,
)
from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import is_system_user
from synthorg.api.dto import ApiResponse
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    AccountLockedError,
    RefreshTokenInvalidError,
    UnauthorizedError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_SESSION_REVOKE_FAILED
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_AUTH_REFRESH_CONSUMED,
    SECURITY_AUTH_REFRESH_REJECTED,
    SECURITY_AUTH_TOKEN_ISSUED,
    SECURITY_SESSION_FORCE_LOGOUT,
    SECURITY_SESSION_LIMIT_ENFORCED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


class AuthSessionController(Controller):
    """Session lifecycle: login, refresh, logout."""

    path = "/auth"
    tags = ("auth",)

    @post(
        "/login",
        status_code=200,
        summary="Authenticate with credentials",
        middleware=[_AUTH_RATE_LIMIT.middleware],
    )
    async def login(
        self,
        data: LoginRequest,
        request: Request[object, object, State],
    ) -> Response[ApiResponse[CookieSessionResponse]]:
        """Validate credentials and set session cookie.

        Returns:
            Result matching the declared return annotation.

        Raises:
            AccountLockedError: Raised on the corresponding failure path.
            UnauthorizedError: Raised on the corresponding failure path.
        """
        app_state = request.app.state["app_state"]
        auth_service: AuthService = auth_service_of(app_state)
        persistence = persistence_of(app_state)

        # Account lockout check (still run dummy hash for timing safety)
        lockout_store = app_state.slice(ApiCoreStateSlice).lockout_store
        if lockout_store is not None and lockout_store.is_locked(data.username):
            await auth_service.verify_password_async(data.password, _DUMMY_ARGON2_HASH)
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="account_locked",
                username=data.username,
            )
            raise AccountLockedError(
                retry_after=lockout_store.lockout_duration_seconds,
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

        if not password_valid or user is None:
            await _record_failed_login(app_state, data.username, request)
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="invalid_credentials",
            )
            msg = "Invalid credentials"
            raise UnauthorizedError(msg)

        # Clear lockout on success.
        await _record_successful_login(app_state, data.username)

        token, expires_in, session_id = auth_service.create_token(user)

        await create_session_record(
            request,
            app_state,
            session_id,
            user,
            expires_in,
        )

        auth_config = get_auth_config(app_state)
        if app_state.slice(ApiCoreStateSlice).session_store is not None:
            revoked = await session_store_of(app_state).enforce_session_limit(
                user.id,
                auth_config.max_concurrent_sessions,
            )
            if revoked:
                logger.info(
                    SECURITY_SESSION_LIMIT_ENFORCED,
                    user_id=user.id,
                    revoked=revoked,
                    max_sessions=auth_config.max_concurrent_sessions,
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
        "/refresh",
        status_code=200,
        summary="Rotate the refresh token",
        middleware=[_AUTH_RATE_LIMIT.middleware],
    )
    async def refresh(
        self,
        request: Request[object, object, State],
    ) -> Response[ApiResponse[CookieSessionResponse]]:
        """Rotate a single-use refresh token into a fresh session JWT.

        Unauthenticated by design: the access token is expected to be
        expired (that is the whole point of refresh), so this path is
        in the auth-middleware exclude set. CSRF double-submit is
        intentionally NOT required here: the refresh cookie is scoped
        to this narrow path with ``SameSite``, and ``consume()`` is a
        single-use compare-and-set that makes a replayed cookie inert.
        The reject matrix + ``SECURITY_AUTH_REFRESH_REJECTED`` audit
        live in :meth:`AuthService.rotate_refresh_token`; this handler
        is a thin cookie adapter (mirrors ``login``).

        Returns:
            Result matching the declared return annotation.

        Raises:
            RefreshTokenInvalidError: Raised on the corresponding failure path.
        """
        app_state = request.app.state["app_state"]
        auth_service: AuthService = auth_service_of(app_state)
        auth_config = get_auth_config(app_state)

        if app_state.slice(ApiCoreStateSlice).refresh_store is None:
            logger.warning(
                SECURITY_AUTH_REFRESH_REJECTED,
                reason="refresh_store_unavailable",
            )
            raise RefreshTokenInvalidError

        raw_refresh = request.cookies.get(auth_config.refresh_cookie_name, "")
        is_revoked = (
            session_store_of(app_state).is_revoked
            if app_state.slice(ApiCoreStateSlice).session_store is not None
            else None
        )
        rotation = await auth_service.rotate_refresh_token(
            raw_refresh_token=raw_refresh,
            refresh_store=refresh_store_of(app_state),
            users=persistence_of(app_state).users,
            is_session_revoked=is_revoked,
        )

        # Same session id: refresh the server-side session record so
        # its expiry tracks the new access token (upsert by id).
        await create_session_record(
            request,
            app_state,
            rotation.session_id,
            rotation.user,
            rotation.expires_in,
        )

        cookies = await make_session_cookies(
            rotation.token,
            rotation.expires_in,
            auth_config,
            app_state=app_state,
            session_id=rotation.session_id,
            user_id=rotation.user.id,
        )
        # Emit AFTER ``make_session_cookies`` persisted the rotated
        # refresh row (state-transition events log post-write).
        logger.info(
            SECURITY_AUTH_REFRESH_CONSUMED,
            user_id=rotation.user.id,
            session_id=rotation.session_id,
        )
        logger.info(
            SECURITY_AUTH_TOKEN_ISSUED,
            user_id=rotation.user.id,
            username=rotation.user.username,
        )
        return Response(
            content=ApiResponse(
                data=CookieSessionResponse(
                    expires_in=rotation.expires_in,
                    must_change_password=rotation.user.must_change_password,
                ),
            ),
            cookies=cookies,
        )

    @post(
        "/logout",
        status_code=204,
        summary="Logout current session",
    )
    async def logout(
        self,
        request: Request[object, object, State],
    ) -> Response[None]:
        """Revoke the current session (if any) and clear cookies.

        Idempotent: always returns 204 with cookie-clearing headers,
        whether or not the caller is authenticated.  This lets clients
        recover from stale cookie state (e.g. an app-version upgrade
        that invalidated the session semantics) without first needing
        a valid session to call logout -- which would be a catch-22.
        Revoking the server-side session record is a best-effort
        extra step when the JWT is still valid.

        Returns:
            ``Response[None]`` instance.
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
        if jti and app_state.slice(ApiCoreStateSlice).session_store is not None:
            # Best-effort revocation -- if the session store is
            # unreachable we still return 204 with cleared cookies
            # so the client can recover from stale state.  Without
            # this, a transient DB error would 500 the logout and
            # trap users in the exact stale-cookie scenario the
            # idempotent contract was designed to fix.
            try:
                revoked = await session_store_of(app_state).revoke(jti)
                if revoked:
                    logger.info(
                        SECURITY_SESSION_FORCE_LOGOUT,
                        session_id=jti,
                        user_id=user_id,
                    )
            except Exception as err:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(err)
                logger.warning(
                    API_SESSION_REVOKE_FAILED,
                    session_id=jti,
                    user_id=user_id,
                    error_type=type(err).__name__,
                    error=safe_error_description(err),
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
