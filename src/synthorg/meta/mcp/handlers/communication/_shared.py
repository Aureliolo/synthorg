"""Shared helpers and arg-validation constants for communication MCP handlers.

The capability-gap mapper and required-arg extractors shared across the
communication sub-domain handler modules (messages, meetings,
connections, webhooks, tunnel).
"""

from typing import TYPE_CHECKING, Any

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common import err
from synthorg.meta.mcp.handlers.common_args import get_optional_str, require_dict
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_TY_STRING = "non-blank string"


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


def _map_capability_not_supported(
    tool: str,
    exc: CapabilityNotSupportedError,
) -> str:
    """Translate facade-side capability gap into a typed envelope.

    Returns:
        Resulting string.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)
