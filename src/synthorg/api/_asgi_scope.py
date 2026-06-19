# module-kind: code
"""ASGI scope-type predicates for the raw middleware layer.

Litestar types ``Scope`` as ``HTTPScope | WebSocketScope`` and narrows
``scope["type"]`` to that closed literal set. At runtime ASGI also delivers
``lifespan`` scopes, so a direct ``scope["type"] == "lifespan"`` comparison in
a middleware trips mypy's ``comparison-overlap``. These predicates read the
discriminant through a plain ``str`` so the comparison is honest about the
wider runtime domain, keeping the call sites ignore-free.
"""

from litestar.types import Scope


def is_http_scope(scope: Scope) -> bool:
    """Return True if *scope* is an HTTP request scope.

    Args:
        scope: The ASGI scope.

    Returns:
        ``True`` when ``scope["type"]`` is ``"http"``.
    """
    scope_type: str = scope["type"]
    return scope_type == "http"


def is_lifespan_scope(scope: Scope) -> bool:
    """Return True if *scope* is an ASGI lifespan scope.

    Args:
        scope: The ASGI scope.

    Returns:
        ``True`` when ``scope["type"]`` is ``"lifespan"``.
    """
    scope_type: str = scope["type"]
    return scope_type == "lifespan"


def is_lifespan_shutdown_message(message: object) -> bool:
    """Return True if *message* is an ASGI ``lifespan.shutdown`` event.

    Args:
        message: A message yielded by an ASGI :data:`~litestar.types.Receive`
            callable.

    Returns:
        ``True`` when *message* is a mapping whose ``type`` is
        ``"lifespan.shutdown"``.
    """
    return isinstance(message, dict) and message.get("type") == "lifespan.shutdown"


__all__ = ["is_http_scope", "is_lifespan_scope", "is_lifespan_shutdown_message"]
