"""Shared utilities for security rule detectors."""

from collections.abc import Iterator  # noqa: TC003

_MAX_RECURSION_DEPTH: int = 20


def walk_string_values(
    arguments: dict[str, object],
    *,
    _depth: int = 0,
) -> Iterator[str]:
    """Yield all string values in a nested dict structure.

    Recurses into nested dicts and lists up to a maximum depth.

    Args:
        arguments: The dict to scan.
    """
    if _depth >= _MAX_RECURSION_DEPTH:
        return
    for value in arguments.values():
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            yield from walk_string_values(value, _depth=_depth + 1)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    yield item
                elif isinstance(item, dict):
                    yield from walk_string_values(item, _depth=_depth + 1)
