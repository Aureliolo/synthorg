"""Integrations-specific argument helpers for integrations MCP handlers.

The optional-string-list and required-int extractors used across the
catalog, OAuth, client, artifact, and ontology handler modules. The
shared single-value extractors (``_require_str`` / ``_require_uuid``),
the capability-gap mapper, and the JSON-coercion helper live in
:mod:`synthorg.meta.mcp.handlers._mcp_handler_common`.
"""

from synthorg.meta.mcp.errors import ArgumentValidationError

_TY_LIST = "sequence of strings"
_TY_INT = "non-negative int"


def _get_list_str(arguments: dict[str, object], key: str) -> tuple[str, ...]:
    """Extract an optional sequence of strings; returns ``()`` when absent.

    Returns:
        Tuple of the string values, or ``()`` when the key is absent.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ArgumentValidationError(key, _TY_LIST)
    for item in raw:
        if not isinstance(item, str):
            raise ArgumentValidationError(key, _TY_LIST)
    return tuple(raw)


def _require_int(arguments: dict[str, object], key: str) -> int:
    """Extract a required non-negative int (rejects bool) or raise.

    Returns:
        Resulting integer.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ArgumentValidationError(key, _TY_INT)
    return raw
