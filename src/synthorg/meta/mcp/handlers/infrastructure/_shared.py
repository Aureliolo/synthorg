"""Shared helpers and arg-validation constants for infrastructure MCP handlers.

The capability-gap mapper, required-arg extractors, and JSON-safe
serialiser shared across the infrastructure sub-domain handler modules
(health, settings, providers, backup, audit-events, users, projects,
requests, setup, simulations, template-packs, integration-health).
"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common import err
from synthorg.meta.mcp.handlers.common_args import (
    get_optional_str,
    require_arg,
    require_dict,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_TY_BACKUP_TRIGGER = "BackupTrigger string"
_ARG_TRIGGER = "trigger"


def _map_capability(tool: str, exc: CapabilityNotSupportedError) -> str:
    """Translate a facade-side capability gap into a typed error envelope.

    Emits :data:`MCP_HANDLER_CAPABILITY_GAP` at INFO so capability
    telemetry is not classified as an invoke failure.

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


def _get_dict(arguments: dict[str, Any], key: str) -> dict[str, str] | None:
    """Extract an optional ``dict[str, str]`` argument; ``None`` when absent.

    Returns:
        The ``dict[str, str]`` value when present, ``None`` otherwise.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    return require_dict(arguments, key, value_type=str)


def _require_uuid(arguments: dict[str, Any], key: str) -> str:
    """Extract a required UUID-shaped string or raise ``ArgumentValidationError``.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    value = require_arg(arguments, key, str)
    try:
        UUID(value)
    except ValueError as exc:
        raise ArgumentValidationError(key, _TY_UUID) from exc
    return value


def _to_jsonable(value: Any) -> Any:
    """Best-effort JSON-safe serialisation for facade returns.

    Pydantic models are dumped via ``model_dump``; other values pass
    through.  Keeps handlers thin when the underlying primitive
    returns a non-uniform shape.

    Returns:
        ``Any`` instance.
    """
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        return dump_fn(mode="json")
    return value
