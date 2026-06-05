"""Shared argument + serialisation helpers for integrations MCP handlers.

Required-string / UUID / int / list extractors, the capability-gap
envelope mapper, and the JSON-coercion helper used across the catalog,
OAuth, client, artifact, and ontology handler modules.
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
_TY_LIST = "sequence of strings"
_TY_INT = "non-negative int"


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


def _get_list_str(arguments: dict[str, Any], key: str) -> tuple[str, ...]:
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


def _require_int(arguments: dict[str, Any], key: str) -> int:
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
