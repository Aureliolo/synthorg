"""Module-level helpers for the auth controller.

Session cookie builders, session record creation, JTI extraction, and
config resolution.  Extracted from ``controller.py`` to keep that
file focused on the Litestar route handlers.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jwt
from litestar.connection import ASGIConnection  # noqa: TC002
from litestar.exceptions import PermissionDeniedException

from synthorg.api.auth.config import AuthConfig
from synthorg.api.auth.cookies import (
    generate_csrf_token,
    make_csrf_cookie,
    make_refresh_cookie,
    make_session_cookie,
)
from synthorg.api.auth.models import AuthenticatedUser
from synthorg.api.auth.session import Session
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_AUTH_CONFIG_FALLBACK,
    API_AUTH_GUARD_SKIPPED,
    API_SESSION_CREATE_FAILED,
)
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_SESSION_CREATED,
)

if TYPE_CHECKING:
    from litestar import Request

    from synthorg.api.auth.models import User
    from synthorg.api.auth.service import AuthService
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_PWD_CHANGE_EXEMPT_SUFFIXES = ("/auth/change-password", "/auth/me")


async def make_session_cookies(  # noqa: PLR0913
    token: str,
    expires_in: int,
    config: AuthConfig,
    *,
    app_state: AppState | None = None,
    session_id: str = "",
    user_id: str = "",
) -> list[Any]:
    """Build the cookie list for a login/setup response.

    Returns session cookie + CSRF cookie, plus a refresh
    cookie when ``jwt_refresh_enabled`` is ``True``.  When
    refresh is enabled the token is also persisted to the
    refresh store for single-use validation.
    """
    cookies: list[Any] = [
        make_session_cookie(token, expires_in, config),
        make_csrf_cookie(generate_csrf_token(), expires_in, config),
    ]
    if config.jwt_refresh_enabled:
        refresh_token = secrets.token_urlsafe(32)
        refresh_max_age = config.jwt_refresh_expiry_minutes * 60
        refresh_persisted = False
        if app_state is not None and session_id and user_id:
            try:
                from synthorg.persistence.auth_protocol import (  # noqa: PLC0415, TC001
                    RefreshTokenRepository as RefreshStore,
                )

                auth_service: AuthService = app_state.auth_service
                token_hash = auth_service.hash_api_key(refresh_token)
                refresh_expiry = datetime.now(UTC) + timedelta(
                    seconds=refresh_max_age,
                )
                store: RefreshStore | None = getattr(app_state, "_refresh_store", None)
                if store is not None:
                    await auth_service.persist_refresh_token(
                        store,
                        token_hash=token_hash,
                        session_id=session_id,
                        user_id=user_id,
                        expires_at=refresh_expiry,
                    )
                    refresh_persisted = True
                else:
                    logger.warning(
                        SECURITY_AUTH_FAILED,
                        reason="refresh_store_not_available",
                    )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    SECURITY_AUTH_FAILED,
                    reason="refresh_token_persist_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        else:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="refresh_token_persist_skipped",
                has_app_state=app_state is not None,
                has_session_id=bool(session_id),
                has_user_id=bool(user_id),
            )
        if refresh_persisted:
            cookies.append(
                make_refresh_cookie(refresh_token, refresh_max_age, config),
            )
    return cookies


def require_password_changed(
    connection: ASGIConnection,  # type: ignore[type-arg]
    _: object,
) -> None:
    """Guard that blocks users who must change their password.

    Paths ending with ``/auth/change-password`` or ``/auth/me``
    are exempt so the user can actually change the password or
    inspect their own profile.
    """
    path = str(connection.url.path)
    if any(path.endswith(s) for s in _PWD_CHANGE_EXEMPT_SUFFIXES):
        logger.debug(
            API_AUTH_GUARD_SKIPPED,
            guard="require_password_changed",
            path=path,
            reason="exempt_suffix",
        )
        return
    user = connection.scope.get("user")
    if user is None:
        scope_type = connection.scope.get("type", "unknown")
        logger.debug(
            API_AUTH_GUARD_SKIPPED,
            guard="require_password_changed",
            path=path,
            scope_type=scope_type,
            reason="no_user_in_scope",
        )
        return
    if not isinstance(user, AuthenticatedUser):
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="unexpected_user_type",
            user_type=type(user).__qualname__,
            path=path,
        )
        raise PermissionDeniedException(detail="Invalid user session")
    if user.must_change_password:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="password_change_required",
            user_id=user.user_id,
            path=path,
        )
        raise PermissionDeniedException(detail="Password change required")


async def create_session_record(
    request: Request[Any, Any, Any],
    app_state: AppState,
    session_id: str,
    user: User,
    expires_in: int,
) -> None:
    """Create a session record after login/setup (non-fatal on failure)."""
    try:
        store = app_state.session_store
        now = datetime.now(UTC)
        client = request.client
        ua = request.headers.get("user-agent", "")[:512]
        session = Session(
            session_id=session_id,
            user_id=user.id,
            username=user.username,
            role=user.role,
            ip_address=client.host if client else "",
            user_agent=ua,
            created_at=now,
            last_active_at=now,
            expires_at=now + timedelta(seconds=expires_in),
        )
        await store.create(session)
        logger.info(
            SECURITY_SESSION_CREATED,
            session_id=session_id,
            user_id=user.id,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_SESSION_CREATE_FAILED,
            reason="Session creation failed (non-fatal)",
            session_id=session_id,
            user_id=user.id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def extract_jti(request: Request[Any, Any, Any]) -> str | None:
    """Extract the JWT ``jti`` claim from cookie or header."""
    app_state = request.app.state["app_state"]
    auth_config = get_auth_config(app_state)

    token = request.cookies.get(auth_config.cookie_name)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]

    try:
        claims = app_state.auth_service.decode_token(token)
    except jwt.InvalidTokenError:
        logger.debug(
            SECURITY_AUTH_FAILED,
            reason="jti_extraction_jwt_error",
        )
        return None
    except Exception as exc:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="jti_extraction_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    else:
        jti: str | None = claims.get("jti")
        return jti


def get_auth_config(app_state: AppState) -> AuthConfig:
    """Return the auth config from app state.

    Returns an ``AuthConfig()`` default when the config graph is missing
    or malformed, after logging the failure at WARNING so the fallback
    never goes unnoticed.  Caller treats the default as fully valid;
    the log line is the operator signal that custom auth tuning did
    not apply.
    """
    try:
        cfg: AuthConfig = app_state.config.api.auth
    except (AttributeError, TypeError) as exc:
        logger.warning(
            API_AUTH_CONFIG_FALLBACK,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return AuthConfig()
    else:
        return cfg
