"""Request-scoped binding for the authenticated user.

The auth middleware (:class:`synthorg.api.auth.middleware.ApiAuthMiddleware`)
populates ``connection.scope["user"]`` with an
:class:`~synthorg.core.auth.models.AuthenticatedUser` after authentication.
:class:`AuthContextMiddleware` runs immediately after auth and binds that
user into the per-:class:`asyncio.Task` :class:`~contextvars.ContextVar`
defined here. Controllers and request-coupled helpers then read the
authenticated user via :func:`get_authenticated_user_id` /
:func:`get_authenticated_user` without threading a ``Request`` argument.

Reading the var while no user is bound raises
:class:`AuthContextMissingError` (a 500): this surfaces middleware
misconfiguration loudly instead of masking it as ``"api"``.

WebSocket scopes use ticket-based authentication
(``synthorg.api.controllers.ws``) and are not handled by this module;
:class:`AuthContextMiddleware` is restricted to HTTP scopes.
"""

from collections.abc import (
    AsyncIterator,  # noqa: TC003 -- runtime use in @asynccontextmanager signature
)
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, ClassVar

from litestar.enums import ScopeType
from litestar.middleware import ASGIMiddleware
from litestar.types import ASGIApp, Receive, Scope, Send  # noqa: TC002

from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AUTH_CONTEXT_BOUND

logger = get_logger(__name__)


_authenticated_user: ContextVar[AuthenticatedUser | None] = ContextVar(
    "synthorg_authenticated_user",
    default=None,
)


class AuthContextMissingError(DomainError):
    """Read attempted on the auth ContextVar with no user bound.

    Surfacing this as a 500 is intentional: the auth middleware runs
    before any controller, so by the time a controller (or helper
    invoked from one) calls :func:`get_authenticated_user_id` the var
    must be set. An unset read is therefore a server bug --
    ``exclude_paths`` misconfiguration, a helper invoked outside the
    request lifecycle, or :class:`AuthContextMiddleware` missing from
    the middleware stack -- not a client error.
    """

    default_message: ClassVar[str] = "Authentication context is not bound"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


def get_authenticated_user() -> AuthenticatedUser:
    """Return the user bound to the active request's ContextVar.

    Raises:
        AuthContextMissingError: When called outside an authenticated
            request scope.
    """
    user = _authenticated_user.get()
    if user is None:
        raise AuthContextMissingError
    return user


def get_authenticated_user_id() -> str:
    """Return the ``user_id`` of the user bound to the current request.

    Raises:
        AuthContextMissingError: When called outside an authenticated
            request scope.
    """
    return get_authenticated_user().user_id


@asynccontextmanager
async def authenticated_user_scope(
    user: AuthenticatedUser,
) -> AsyncIterator[None]:
    """Bind ``user`` to the auth ContextVar for the duration of the block.

    Production binding is performed by :class:`AuthContextMiddleware`.
    This helper exists for tests, background tasks, and any caller that
    needs to invoke a request-coupled helper outside the HTTP request
    path. Mirrors :func:`synthorg.providers.cost_recording.cost_recording_scope`
    -- token-based reset for exception safety, restoring whatever was
    active before.
    """
    token = _authenticated_user.set(user)
    try:
        yield
    finally:
        _authenticated_user.reset(token)


class AuthContextMiddleware(ASGIMiddleware):
    """Bind ``scope["user"]`` into the per-task ContextVar.

    Runs immediately after :class:`~synthorg.api.auth.middleware.ApiAuthMiddleware`
    so authenticated handlers, downstream middleware, and helpers can
    read the user via :func:`get_authenticated_user_id` without
    threading a ``Request``. Excluded paths (where ``ApiAuthMiddleware``
    skipped) leave the var at its default ``None``; helpers reading it
    raise :class:`AuthContextMissingError`, which is the desired
    behaviour for endpoints that should never have reached a
    user-coupled helper without authentication.

    HTTP-only: WebSocket scopes use ticket-based authentication and are
    bypassed by the ``scopes`` filter on the base class.
    """

    scopes: tuple[ScopeType, ...] = (ScopeType.HTTP,)

    async def handle(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        next_app: ASGIApp,
    ) -> None:
        """Bind ``scope["user"]`` for the duration of the inner dispatch."""
        scope_user: Any = scope.get("user")
        if not isinstance(scope_user, AuthenticatedUser):
            await next_app(scope, receive, send)
            return
        token = _authenticated_user.set(scope_user)
        logger.debug(
            API_AUTH_CONTEXT_BOUND,
            user_id=scope_user.user_id,
            path=scope.get("path", ""),
        )
        try:
            await next_app(scope, receive, send)
        finally:
            _authenticated_user.reset(token)
