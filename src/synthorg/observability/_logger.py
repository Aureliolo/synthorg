"""Convenience wrapper for structured logger creation."""

from typing import Any

import structlog


def get_logger(name: str, **initial_bindings: Any) -> structlog.stdlib.BoundLogger:
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
        A bound structlog logger.
    """
    return structlog.get_logger(name, **initial_bindings)  # type: ignore[no-any-return]
