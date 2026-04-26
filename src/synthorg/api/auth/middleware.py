"""JWT + API key authentication middleware."""

import hashlib
import hmac as _hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import jwt
from litestar.enums import ScopeType
from litestar.exceptions import NotAuthorizedException
from litestar.middleware import (
    AbstractAuthenticationMiddleware,
    AuthenticationResult,
)

from synthorg.api.auth.models import AuthenticatedUser, AuthMethod
from synthorg.api.auth.service import SecretNotConfiguredError
from synthorg.api.auth.system_user import SYSTEM_AUDIENCE, SYSTEM_ISSUER
from synthorg.api.guards import HumanRole
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_AUTH_COOKIE_NAME_FALLBACK,
    API_AUTH_COOKIE_USED,
)
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_AUTH_SUCCESS,
)

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection

    from synthorg.api.auth.config import AuthConfig
    from synthorg.api.auth.models import ApiKey
    from synthorg.api.auth.service import AuthService
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_BEARER_PARTS = 2
_DEFAULT_COOKIE_NAME = "session"


def _get_cookie_name(app_state: AppState) -> str:
    """Return the configured session cookie name.

    Falls back to the default ``"session"`` when the config is not
    available on the app state (e.g. in minimal test fixtures), after
    logging the failure at WARNING so the fallback is observable.
    The middleware hot path uses this on every request, so a broken
    config graph would otherwise silently break cookie lookup for
    operators with a custom ``auth.cookie_name``.

    Args:
        app_state: Application state container.

    Returns:
        Session cookie name string.
    """
    try:
        return str(app_state.config.api.auth.cookie_name)
    except (AttributeError, TypeError) as exc:
        logger.warning(
            API_AUTH_COOKIE_NAME_FALLBACK,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _DEFAULT_COOKIE_NAME


class ApiAuthMiddleware(AbstractAuthenticationMiddleware):
    """Authenticate requests via cookie, JWT header, or API key.

    Authentication priority:

    1. **Session cookie** -- HttpOnly cookie set by login/setup.
       Primary auth path for browser sessions.
    2. **Authorization header** -- ``Bearer <token>``.
       Tokens with dots are JWTs (system user CLI tokens).
       Tokens without dots are API keys (HMAC-SHA256 lookup).

    Requires ``auth_service``, persistence backend on
    ``app.state["app_state"]``.
    """

    async def authenticate_request(
        self,
        connection: ASGIConnection[Any, Any, Any, Any],
    ) -> AuthenticationResult:
        """Validate the session cookie or Authorization header.

        Tries the session cookie first.  Falls back to the
        Authorization header for API keys and system user JWTs.

        Args:
            connection: Incoming ASGI connection.

        Returns:
            AuthenticationResult with AuthenticatedUser.

        Raises:
            NotAuthorizedException: If authentication fails.
        """
        app_state = connection.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service
        path = str(connection.url.path)

        # 1. Try session cookie (primary path for browser sessions)
        cookie_name = _get_cookie_name(app_state)
        session_cookie = connection.cookies.get(cookie_name)
        if session_cookie and "." in session_cookie:
            user = await _try_jwt_auth(
                session_cookie,
                auth_service,
                app_state,
                path,
            )
            if user is not None:
                logger.debug(
                    API_AUTH_COOKIE_USED,
                    user_id=user.user_id,
                    path=path,
                )
                return AuthenticationResult(user=user, auth=session_cookie)

        if session_cookie:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="cookie_jwt_invalid",
                path=path,
            )

        # 2. Fall back to Authorization header (API keys, system user)
        auth_header = connection.headers.get("authorization")
        if not auth_header:
            if session_cookie:
                # Cookie was present but invalid
                raise NotAuthorizedException(
                    detail="Invalid session cookie",
                )
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="missing_authentication",
                path=path,
            )
            raise NotAuthorizedException(
                detail="Missing authentication",
            )

        token = _extract_bearer_token(auth_header)
        if token is None:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="invalid_scheme",
                path=path,
            )
            raise NotAuthorizedException(
                detail="Invalid authorization scheme",
            )

        if "." in token:
            user = await _try_jwt_auth(
                token,
                auth_service,
                app_state,
                path,
            )
            if user is not None:
                return AuthenticationResult(user=user, auth=token)
            raise NotAuthorizedException(detail="Invalid JWT token")

        user = await _try_api_key_auth(
            token,
            auth_service,
            app_state,
            path,
        )
        if user is not None:
            return AuthenticationResult(user=user, auth=token)
        raise NotAuthorizedException(detail="Invalid credentials")


def _extract_bearer_token(header: str) -> str | None:
    """Extract token from ``Bearer <token>`` header value."""
    parts = header.split(None, 1)
    if len(parts) != _BEARER_PARTS or parts[0].lower() != "bearer":
        return None
    return parts[1]


async def _try_jwt_auth(
    token: str,
    auth_service: AuthService,
    app_state: AppState,
    path: str,
) -> AuthenticatedUser | None:
    """Attempt JWT authentication.

    Validates the token signature, expiry, and required claims.
    Delegates user resolution and ``pwd_sig`` validation to
    :func:`_resolve_jwt_user`.

    Returns:
        Authenticated user on success, or ``None`` on failure.
    """
    try:
        claims = auth_service.decode_token(token)
    except jwt.InvalidTokenError as exc:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="jwt_invalid",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            path=path,
        )
        return None
    except SecretNotConfiguredError as exc:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="jwt_secret_not_configured",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            path=path,
        )
        return None

    # Check session revocation (sync, O(1) in-memory lookup).
    jti = claims.get("jti")
    if jti and app_state.has_session_store:
        session_store = app_state.session_store
        if session_store.is_revoked(jti):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="session_revoked",
                jti=jti[:8],
                path=path,
            )
            return None

    return await _resolve_jwt_user(claims, app_state, path)


async def _resolve_jwt_user(
    claims: dict[str, Any],
    app_state: AppState,
    path: str,
) -> AuthenticatedUser | None:
    """Resolve user from JWT claims and validate ``pwd_sig``.

    The ``pwd_sig`` is a plain SHA-256 truncation (not HMAC) of
    the stored password hash, protected by the JWT signature.

    System users (CLI tokens) skip ``pwd_sig`` validation because
    their random password hash is never known to any caller.
    The JWT signature alone authenticates these tokens.
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.warning(SECURITY_AUTH_FAILED, reason="jwt_missing_sub", path=path)
        return None

    db_user = await app_state.persistence.users.get(user_id)
    if db_user is None:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="jwt_user_not_found",
            user_id=user_id,
            path=path,
        )
        return None

    # System users have a random password hash nobody knows --
    # pwd_sig validation is meaningless and skipped.  The shared
    # JWT secret signature is the sole authentication gate.
    # Additionally, require iss + aud to constrain which tokens
    # may skip pwd_sig.
    if db_user.role == HumanRole.SYSTEM:
        if claims.get("iss") != SYSTEM_ISSUER:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="system_token_wrong_issuer",
                user_id=user_id,
                iss=claims.get("iss"),
                path=path,
            )
            return None
        if claims.get("aud") != SYSTEM_AUDIENCE:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="system_token_wrong_audience",
                user_id=user_id,
                aud=claims.get("aud"),
                path=path,
            )
            return None
    else:
        expected_sig = hashlib.sha256(
            db_user.password_hash.encode(),
        ).hexdigest()[:16]
        if not _hmac.compare_digest(
            claims.get("pwd_sig", ""),
            expected_sig,
        ):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="password_changed_since_token_issued",
                user_id=user_id,
                path=path,
            )
            return None

    logger.info(
        SECURITY_AUTH_SUCCESS,
        user_id=db_user.id,
        username=db_user.username,
        auth_method="jwt",
        path=path,
    )
    return AuthenticatedUser(
        user_id=db_user.id,
        username=db_user.username,
        role=db_user.role,
        auth_method=AuthMethod.JWT,
        must_change_password=db_user.must_change_password,
        org_roles=db_user.org_roles,
        scoped_departments=db_user.scoped_departments,
    )


async def _try_api_key_auth(
    token: str,
    auth_service: AuthService,
    app_state: AppState,
    path: str,
) -> AuthenticatedUser | None:
    """Attempt API key authentication.

    Requires the JWT secret to be configured (used as the HMAC
    key for hashing).  Returns ``None`` gracefully if the secret
    is missing.

    Returns:
        Authenticated user on success, or ``None`` on failure.
    """
    try:
        key_hash = auth_service.hash_api_key(token)
    except SecretNotConfiguredError as exc:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="api_key_hash_failed_secret_not_configured",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            path=path,
        )
        return None

    api_key = await app_state.persistence.api_keys.get_by_hash(key_hash)
    if api_key is None:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="api_key_not_found",
            path=path,
        )
        return None
    return await _resolve_api_key_user(api_key, app_state, path)


async def _resolve_api_key_user(
    api_key: ApiKey,
    app_state: AppState,
    path: str,
) -> AuthenticatedUser | None:
    """Validate an API key (revocation, expiry) and resolve its owner."""
    if api_key.revoked:
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="api_key_revoked",
            key_name=api_key.name,
            path=path,
        )
        return None
    if api_key.expires_at is not None and api_key.expires_at < datetime.now(UTC):
        logger.warning(
            SECURITY_AUTH_FAILED,
            reason="api_key_expired",
            key_name=api_key.name,
            path=path,
        )
        return None

    db_user = await app_state.persistence.users.get(api_key.user_id)
    if db_user is None:
        logger.error(
            SECURITY_AUTH_FAILED,
            reason="api_key_orphaned",
            key_name=api_key.name,
            user_id=api_key.user_id,
            path=path,
        )
        return None

    logger.info(
        SECURITY_AUTH_SUCCESS,
        user_id=db_user.id,
        username=db_user.username,
        auth_method="api_key",
        key_name=api_key.name,
        path=path,
    )
    return AuthenticatedUser(
        user_id=db_user.id,
        username=db_user.username,
        role=api_key.role,
        auth_method=AuthMethod.API_KEY,
        must_change_password=db_user.must_change_password,
        org_roles=(),
        scoped_departments=(),
    )


def create_auth_middleware_class(
    auth_config: AuthConfig,
) -> type[ApiAuthMiddleware]:
    """Create a middleware class with excluded paths baked in.

    Litestar's ``AbstractAuthenticationMiddleware.__init__`` takes
    ``exclude`` as a parameter (default ``None``).  We create a
    subclass whose ``__init__`` forwards the configured exclude
    list to ``super().__init__``.

    The middleware is restricted to ``ScopeType.HTTP`` only --
    WebSocket connections use ticket-based auth handled entirely
    inside the WS handler (see ``controllers/ws.py``).

    Args:
        auth_config: Auth configuration with exclude_paths.

    Returns:
        Middleware class ready for use in the Litestar middleware stack.
    """
    exclude_paths = (
        list(auth_config.exclude_paths) if auth_config.exclude_paths else None
    )

    class ConfiguredAuthMiddleware(ApiAuthMiddleware):
        """Auth middleware with pre-configured exclude paths."""

        def __init__(self, app: Any) -> None:
            super().__init__(
                app,
                exclude=exclude_paths,
                scopes={ScopeType.HTTP},
            )

    return ConfiguredAuthMiddleware
