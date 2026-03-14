"""JWT + API key authentication middleware."""

import hashlib
import hmac as _hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import jwt
from litestar.exceptions import NotAuthorizedException
from litestar.middleware import (
    AbstractAuthenticationMiddleware,
    AuthenticationResult,
)

from synthorg.api.auth.models import AuthenticatedUser, AuthMethod
from synthorg.api.auth.service import SecretNotConfiguredError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_FAILED,
    API_AUTH_SUCCESS,
)

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection

    from synthorg.api.auth.config import AuthConfig
    from synthorg.api.auth.models import ApiKey
    from synthorg.api.auth.service import AuthService
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_BEARER_PARTS = 2


def _validate_auth_header(
    connection: ASGIConnection[Any, Any, Any, Any],
) -> str:
    """Extract and validate the bearer token from the request.

    Returns:
        The bearer token string.

    Raises:
        NotAuthorizedException: On missing or invalid header.
    """
    path = str(connection.url.path)
    auth_header = connection.headers.get("authorization")
    if not auth_header:
        logger.warning(
            API_AUTH_FAILED,
            reason="missing_header",
            path=path,
        )
        raise NotAuthorizedException(
            detail="Missing Authorization header",
        )
    token = _extract_bearer_token(auth_header)
    if token is None:
        logger.warning(
            API_AUTH_FAILED,
            reason="invalid_scheme",
            path=path,
        )
        raise NotAuthorizedException(
            detail="Invalid authorization scheme",
        )
    return token


class ApiAuthMiddleware(AbstractAuthenticationMiddleware):
    """Authenticate requests via JWT or API key.

    Reads ``Authorization: Bearer <token>`` from the request.
    Tokens containing ``.`` are treated exclusively as JWTs.
    Tokens without dots are tried as API keys via HMAC-SHA256
    hash lookup.

    Requires ``auth_service``, persistence backend on
    ``app.state["app_state"]``.
    """

    async def authenticate_request(
        self,
        connection: ASGIConnection[Any, Any, Any, Any],
    ) -> AuthenticationResult:
        """Validate the Authorization header.

        Args:
            connection: Incoming ASGI connection.

        Returns:
            AuthenticationResult with AuthenticatedUser.

        Raises:
            NotAuthorizedException: If authentication fails.
        """
        token = _validate_auth_header(connection)
        app_state = connection.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service
        path = str(connection.url.path)

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
            API_AUTH_FAILED,
            reason="jwt_invalid",
            error_type=type(exc).__qualname__,
            error=str(exc),
            path=path,
        )
        return None
    except SecretNotConfiguredError:
        logger.exception(
            API_AUTH_FAILED,
            reason="jwt_secret_not_configured",
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
    """
    user_id = claims.get("sub")
    if not user_id:
        logger.warning(API_AUTH_FAILED, reason="jwt_missing_sub", path=path)
        return None

    db_user = await app_state.persistence.users.get(user_id)
    if db_user is None:
        logger.warning(
            API_AUTH_FAILED,
            reason="jwt_user_not_found",
            user_id=user_id,
            path=path,
        )
        return None

    expected_sig = hashlib.sha256(db_user.password_hash.encode()).hexdigest()[:16]
    if not _hmac.compare_digest(claims.get("pwd_sig", ""), expected_sig):
        logger.warning(
            API_AUTH_FAILED,
            reason="password_changed_since_token_issued",
            user_id=user_id,
            path=path,
        )
        return None

    logger.info(
        API_AUTH_SUCCESS,
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
    except SecretNotConfiguredError:
        logger.exception(
            API_AUTH_FAILED,
            reason="api_key_hash_failed_secret_not_configured",
            path=path,
        )
        return None

    api_key = await app_state.persistence.api_keys.get_by_hash(key_hash)
    if api_key is None:
        logger.warning(
            API_AUTH_FAILED,
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
            API_AUTH_FAILED,
            reason="api_key_revoked",
            key_name=api_key.name,
            path=path,
        )
        return None
    if api_key.expires_at is not None and api_key.expires_at < datetime.now(UTC):
        logger.warning(
            API_AUTH_FAILED,
            reason="api_key_expired",
            key_name=api_key.name,
            path=path,
        )
        return None

    db_user = await app_state.persistence.users.get(api_key.user_id)
    if db_user is None:
        logger.error(
            API_AUTH_FAILED,
            reason="api_key_orphaned",
            key_name=api_key.name,
            user_id=api_key.user_id,
            path=path,
        )
        return None

    logger.info(
        API_AUTH_SUCCESS,
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
    )


def create_auth_middleware_class(
    auth_config: AuthConfig,
) -> type[ApiAuthMiddleware]:
    """Create a middleware class with excluded paths baked in.

    Litestar's ``AbstractAuthenticationMiddleware.__init__`` takes
    ``exclude`` as a parameter (default ``None``).  We create a
    subclass whose ``__init__`` forwards the configured exclude
    list to ``super().__init__``.

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
            super().__init__(app, exclude=exclude_paths)

    return ConfiguredAuthMiddleware
