"""Shared argument + serialisation helpers for organization MCP handlers.

Required-string / UUID / list extractors, the capability-gap envelope
mapper, and the JSON-coercion helper used across the company,
department, team, and role-version handler modules.
"""

from typing import Any
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common import err
from synthorg.meta.mcp.handlers.common_args import get_optional_str, require_arg
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

logger = get_logger(__name__)

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_TY_DICT = "mapping of str -> object"
_TY_LIST = "sequence of strings"


def _map_capability(tool: str, exc: CapabilityNotSupportedError) -> str:
    """Translate a facade-side capability gap into a typed error envelope.

    Emits :data:`MCP_HANDLER_CAPABILITY_GAP` so capability telemetry is
    distinct from invoke failures.

    Returns:
        Resulting string.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)


def _require_str(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required non-blank string or raise ``ArgumentValidationError``.

    Returns:
        ``NotBlankStr`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    value = get_optional_str(arguments, key)
    if value is None:
        raise ArgumentValidationError(key, _TY_STRING)
    return value


def _require_uuid(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required UUID-shaped string or raise ``ArgumentValidationError``.

    Returns:
        ``NotBlankStr`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    value = require_arg(arguments, key, str)
    try:
        UUID(value)
    except ValueError as exc:
        raise ArgumentValidationError(key, _TY_UUID) from exc
    return NotBlankStr(value)


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


def _to_jsonable(value: Any) -> Any:
    """Coerce a Pydantic / ``to_dict`` value into a JSON-serialisable form.

    Returns:
        ``Any`` instance.
    """
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        return dump_fn(mode="json")
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value
