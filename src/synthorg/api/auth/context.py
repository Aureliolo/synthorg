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

from synthorg.core.actor_context import (
    ActorIdentity,
    ActorKind,
    actor_scope,
)
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_AUTH_CONTEXT_BOUND,
    API_AUTH_CONTEXT_MISSING,
    API_AUTH_CONTEXT_SKIPPED,
)

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
        # An unset read is a server-side wiring bug (excluded path
        # misrouted to a request-coupled helper, AuthContextMiddleware
        # missing from the stack, helper invoked outside a request).
        # Operators see only the 500 envelope without this breadcrumb,
        # so emit a structured event before raising.
        logger.warning(API_AUTH_CONTEXT_MISSING, caller="get_authenticated_user")
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

    Example (background task that calls a request-coupled helper)::

        async def _background_audit(user: AuthenticatedUser) -> None:
            async with authenticated_user_scope(user):
                # audit_actor_from_context() now returns this user's
                # ProviderAuditActor without raising.
                actor = audit_actor_from_context()
                ...
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
        bound_user: AuthenticatedUser | None
        if isinstance(scope_user, AuthenticatedUser):
            bound_user = scope_user
            logger.debug(
                API_AUTH_CONTEXT_BOUND,
                user_id=scope_user.user_id,
                path=scope.get("path", ""),
            )
        else:
            # Excluded paths legitimately have no scope.user; a present
            # value of any other type means a downstream middleware
            # mutated it or auth was reordered, which is a wiring bug
            # the operator must see.
            bound_user = None
            if scope_user is not None:
                logger.warning(
                    API_AUTH_CONTEXT_SKIPPED,
                    scope_user_type=type(scope_user).__name__,
                    path=scope.get("path", ""),
                )
                # Normalise the request scope to match the bound
                # ContextVar so downstream layers reading scope["user"]
                # directly (rate-limit identifiers, anonymous-tier
                # gate, etc.) see the same unauthenticated state as
                # get_authenticated_user*(). Without this, a foreign
                # principal would be visible to gates while the
                # accessors raise AuthContextMissingError.
                scope["user"] = None
        # Always bind a token (None on the skipped path) so a context
        # inherited from an outer task cannot leak a stale principal
        # into helpers reading the var; reset unconditionally restores
        # the prior binding.
        token = _authenticated_user.set(bound_user)
        try:
            if bound_user is not None:
                # Bind the actor seam (RFC#3 / ADR-0003) so decision
                # leaves resolve ``decided_by`` via ``current_actor()``
                # instead of every caller threading it. ``actor_id`` is
                # the immutable user id; ``label`` is the human-readable
                # username recorded in audit rows.
                actor = ActorIdentity(
                    actor_id=bound_user.user_id,
                    kind=ActorKind.HUMAN,
                    label=bound_user.username,
                )
                with actor_scope(actor):
                    await next_app(scope, receive, send)
            else:
                await next_app(scope, receive, send)
        finally:
            _authenticated_user.reset(token)
