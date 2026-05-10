"""HTTP-coupled authentication layer.

Domain types (``AuthConfig``, ``HumanRole``, ``User``,
``AuthenticatedUser``, ``Session``, ``RefreshRecord``, etc.) live in
``synthorg.core.auth``. This package keeps only the components that
bind to Litestar / JWT issuer-audience constants:
``AuthService``, ``WsTicketStore``, the controllers, and the
authentication / CSRF middleware.
"""

from synthorg.api.auth.context import (
    AuthContextMiddleware,
    AuthContextMissingError,
    authenticated_user_scope,
    get_authenticated_user,
    get_authenticated_user_id,
)

__all__ = [
    "AuthContextMiddleware",
    "AuthContextMissingError",
    "authenticated_user_scope",
    "get_authenticated_user",
    "get_authenticated_user_id",
]
