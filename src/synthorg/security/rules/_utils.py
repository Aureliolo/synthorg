"""Shared utilities for security rule detectors."""

from collections.abc import Iterator  # noqa: TC003

from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_SCAN_DEPTH_EXCEEDED

logger = get_logger(__name__)

_MAX_RECURSION_DEPTH: int = 20


def walk_string_values(
    arguments: dict[str, object],
    *,
    _depth: int = 0,
) -> Iterator[str]:
    """Yield all string values in a nested dict/list structure.

    Recurses into nested dicts and lists up to a maximum depth of 20.
    Non-string, non-dict, non-list values are silently skipped.

    Args:
        arguments: The dict to scan.
    """
    if _depth >= _MAX_RECURSION_DEPTH:
        logger.warning(
            SECURITY_SCAN_DEPTH_EXCEEDED,
            depth=_depth,
            max_depth=_MAX_RECURSION_DEPTH,
        )
        return
    for value in arguments.values():
        yield from _walk_value(value, _depth=_depth)


def _walk_value(value: object, *, _depth: int) -> Iterator[str]:
    """Yield strings from a single value, recursing into dicts and lists."""
    if _depth >= _MAX_RECURSION_DEPTH:
        logger.warning(
            SECURITY_SCAN_DEPTH_EXCEEDED,
            depth=_depth,
            max_depth=_MAX_RECURSION_DEPTH,
        )
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        yield from walk_string_values(value, _depth=_depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_value(item, _depth=_depth + 1)
