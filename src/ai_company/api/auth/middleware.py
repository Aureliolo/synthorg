"""JWT + API key authentication middleware."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import jwt
from litestar.exceptions import NotAuthorizedException
from litestar.middleware import (
    AbstractAuthenticationMiddleware,
    AuthenticationResult,
)

from ai_company.api.auth.models import AuthenticatedUser, AuthMethod
from ai_company.api.auth.service import AuthService
from ai_company.api.guards import HumanRole
from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_AUTH_FAILED,
    API_AUTH_SUCCESS,
)

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection

    from ai_company.api.auth.config import AuthConfig

logger = get_logger(__name__)

_BEARER_PARTS = 2


class ApiAuthMiddleware(AbstractAuthenticationMiddleware):
    """Authenticate requests via JWT or API key.

    Reads ``Authorization: Bearer <token>`` from the request.
    Tokens containing ``.`` are tried as JWTs first; if that fails
    (or the token has no dots), it is tried as an API key via
    SHA-256 hash lookup.

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
        auth_header = connection.headers.get("authorization")
        if not auth_header:
            logger.warning(
                API_AUTH_FAILED,
                reason="missing_header",
                path=str(connection.url.path),
            )
            raise NotAuthorizedException(detail="Missing Authorization header")

        token = _extract_bearer_token(auth_header)
        if token is None:
            logger.warning(
                API_AUTH_FAILED,
                reason="invalid_scheme",
                path=str(connection.url.path),
            )
            raise NotAuthorizedException(detail="Invalid authorization scheme")

        app_state = connection.app.state["app_state"]
        auth_service: AuthService = app_state.auth_service

        # Try JWT first (tokens with dots are likely JWTs)
        if "." in token:
            user = await _try_jwt_auth(token, auth_service, app_state, connection)
            if user is not None:
                return AuthenticationResult(user=user, auth=token)

        # Fall back to API key
        user = await _try_api_key_auth(token, app_state, connection)
        if user is not None:
            return AuthenticationResult(user=user, auth=token)

        logger.warning(
            API_AUTH_FAILED,
            reason="invalid_credentials",
            path=str(connection.url.path),
        )
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
    app_state: Any,
    connection: ASGIConnection[Any, Any, Any, Any],
) -> AuthenticatedUser | None:
    """Attempt JWT authentication."""
    try:
        claims = auth_service.decode_token(token)
    except jwt.InvalidTokenError:
        return None

    user_id = claims.get("sub")
    if not user_id:
        return None

    # Verify the user still exists
    persistence = app_state.persistence
    db_user = await persistence.users.get(user_id)
    if db_user is None:
        return None

    authenticated = AuthenticatedUser(
        user_id=db_user.id,
        username=db_user.username,
        role=db_user.role,
        auth_method=AuthMethod.JWT,
        must_change_password=db_user.must_change_password,
    )
    logger.info(
        API_AUTH_SUCCESS,
        user_id=db_user.id,
        username=db_user.username,
        auth_method="jwt",
        path=str(connection.url.path),
    )
    return authenticated


async def _try_api_key_auth(
    token: str,
    app_state: Any,
    connection: ASGIConnection[Any, Any, Any, Any],
) -> AuthenticatedUser | None:
    """Attempt API key authentication."""
    key_hash = AuthService.hash_api_key(token)
    persistence = app_state.persistence
    api_key = await persistence.api_keys.get_by_hash(key_hash)
    if api_key is None:
        return None

    # Check revocation and expiry
    if api_key.revoked:
        return None
    if api_key.expires_at is not None and api_key.expires_at < datetime.now(UTC):
        return None

    # Look up the owning user
    db_user = await persistence.users.get(api_key.user_id)
    if db_user is None:
        return None

    authenticated = AuthenticatedUser(
        user_id=db_user.id,
        username=db_user.username,
        role=HumanRole(api_key.role),
        auth_method=AuthMethod.API_KEY,
        must_change_password=db_user.must_change_password,
    )
    logger.info(
        API_AUTH_SUCCESS,
        user_id=db_user.id,
        username=db_user.username,
        auth_method="api_key",
        key_name=api_key.name,
        path=str(connection.url.path),
    )
    return authenticated


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
    exclude_paths = list(auth_config.exclude_paths) or None

    class ConfiguredAuthMiddleware(ApiAuthMiddleware):
        """Auth middleware with pre-configured exclude paths."""

        def __init__(self, app: Any) -> None:
            super().__init__(app, exclude=exclude_paths)

    return ConfiguredAuthMiddleware
