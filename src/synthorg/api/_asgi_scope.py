# module-kind: code
"""ASGI scope / message type-narrowing helpers.

Litestar types ``Scope`` as ``HTTPScope | WebSocketScope`` and its
receive messages as the HTTP/WebSocket event union, but at runtime the
ASGI server also delivers ``lifespan`` scopes and ``lifespan.shutdown``
messages that sit outside those static unions. These helpers read the
runtime ``type`` discriminator through a widened ``str`` so the literal
comparison expresses the runtime branch without tripping mypy's
``comparison-overlap`` check at every call site.
"""

from collections.abc import Mapping

from litestar.types import Scope


def is_http_scope(scope: Scope) -> bool:
    """Return True when the ASGI scope is an HTTP request scope.

    Args:
        scope: The ASGI scope to inspect.

    Returns:
        ``True`` when ``scope["type"]`` is ``"http"``.
    """
    scope_type: str = scope.get("type", "")
    return scope_type == "http"


def is_lifespan_scope(scope: Scope) -> bool:
    """Return True when the ASGI scope is a lifespan scope.

    Args:
        scope: The ASGI scope to inspect.

    Returns:
        ``True`` when ``scope["type"]`` is ``"lifespan"``.
    """
    scope_type: str = scope.get("type", "")
    return scope_type == "lifespan"


def is_lifespan_shutdown_message(message: object) -> bool:
    """Return True when an ASGI receive message is ``lifespan.shutdown``.

    Args:
        message: A message yielded by an ASGI receive callable.

    Returns:
        ``True`` when *message* is a mapping whose ``type`` is
        ``"lifespan.shutdown"``.
    """
    if not isinstance(message, Mapping):
        return False
    return message.get("type") == "lifespan.shutdown"


__all__ = ["is_http_scope", "is_lifespan_scope", "is_lifespan_shutdown_message"]
