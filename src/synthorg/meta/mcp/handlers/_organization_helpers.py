"""Organization-specific argument helpers for organization MCP handlers.

The required-string-list and required-UUID-list extractors used across
the company, department, team, and role-version handler modules. The
shared single-value extractors (``_require_str`` / ``_require_uuid``),
the capability-gap mapper, and the JSON-coercion helper live in
:mod:`synthorg.meta.mcp.handlers._mcp_handler_common`.
"""

from typing import Any
from uuid import UUID

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import ArgumentValidationError

_TY_UUID = "UUID string"
_TY_LIST = "sequence of strings"


def _require_str_list(arguments: dict[str, Any], key: str) -> tuple[str, ...]:
    """Extract a required sequence of non-blank strings, or raise on error.

    Returns:
        Tuple of the validated non-blank strings.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if not isinstance(raw, (list, tuple)):
        raise ArgumentValidationError(key, _TY_LIST)
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ArgumentValidationError(key, _TY_LIST)
    return tuple(raw)


def _require_uuid_list(
    arguments: dict[str, Any],
    key: str,
) -> tuple[NotBlankStr, ...]:
    """Extract a required sequence of UUID-shaped strings.

    Each entry is validated with :func:`UUID` so malformed IDs never
    reach the mutation service.

    Returns:
        Tuple of the validated UUID-shaped strings.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    entries = _require_str_list(arguments, key)
    for entry in entries:
        try:
            UUID(entry)
        except ValueError as exc:
            raise ArgumentValidationError(key, _TY_UUID) from exc
    return tuple(NotBlankStr(e) for e in entries)
