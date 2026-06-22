"""Shared helpers and arg-validation constants for infrastructure MCP handlers.

The capability-gap mapper, required-arg extractors, and JSON-safe
serialiser shared across the infrastructure sub-domain handler modules
(health, settings, providers, backup, audit-events, users, projects,
requests, setup, simulations, template-packs, integration-health).
"""

from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    _to_jsonable as _canonical_to_jsonable,
)
from synthorg.meta.mcp.handlers.common import err
from synthorg.meta.mcp.handlers.common_args import require_arg, require_dict
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

logger = get_logger(__name__)

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


def _get_dict(arguments: dict[str, object], key: str) -> dict[str, str] | None:
    """Extract an optional ``dict[str, str]`` argument; ``None`` when absent.

    Returns:
        The ``dict[str, str]`` value when present, ``None`` otherwise.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    return require_dict(arguments, key, value_type=str)


def _require_uuid(arguments: dict[str, object], key: str) -> str:
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


def _to_jsonable(value: object) -> object:
    """Coerce a facade return into a JSON-serialisable form.

    Thin adapter over the canonical
    :func:`synthorg.meta.mcp.handlers._mcp_handler_common._to_jsonable`
    so the infrastructure handlers keep importing serialisation from this
    shared module while the coercion logic lives in one place.

    Returns:
        JSON-serialisable representation of ``value``.
    """
    return _canonical_to_jsonable(value)
