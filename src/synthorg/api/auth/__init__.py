"""HTTP-coupled authentication layer.

Domain types (``AuthConfig``, ``HumanRole``, ``User``,
``AuthenticatedUser``, ``Session``, ``RefreshRecord``, etc.) live in
``synthorg.core.auth``. This package keeps only the components that
bind to Litestar / JWT issuer-audience constants:
``AuthService``, ``WsTicketStore``, the controllers, and the
authentication / CSRF middleware.
"""
