"""Convenience wrapper for structured logger creation."""

from typing import Any

import structlog


def get_logger(name: str, **initial_bindings: object) -> Any:
    """Get a structured logger bound to the given name.

    Thin wrapper over :func:`structlog.get_logger` that ensures
    consistent logger creation across the codebase.

    Usage::

        from synthorg.observability import get_logger

        logger = get_logger(__name__)
        logger.info("something happened", key="value")

    Args:
        name: Logger name, typically ``__name__``.
        **initial_bindings: Key-value pairs bound to every log entry.

    Returns:
        A structlog logger. The return is typed ``Any`` to match
        structlog's own ``get_logger`` signature: the call returns a
        ``BoundLoggerLazyProxy`` at module import time (pre-configure)
        and a real wrapper-class instance after binding, with attribute
        lookup forwarded via ``__getattr__``. typeguard's Protocol
        check inspects class-level signatures and so cannot match a
        ``__getattr__``-delegation pattern; declaring the return as a
        concrete type (``BoundLogger``) or a custom Protocol both fail
        at runtime even though calls on the returned object work
        transparently. ``Any`` reflects structlog's own deliberate
        typing of a fundamentally untypeable lazy proxy.
    """
    return structlog.get_logger(name, **initial_bindings)
