"""Shared helpers and arg-validation constants for communication MCP handlers.

The capability-gap mapper and required-arg extractors shared across the
communication sub-domain handler modules (messages, meetings,
connections, webhooks, tunnel).
"""

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.meta.mcp.handlers.common import err
from synthorg.meta.mcp.handlers.common_args import require_dict
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_CAPABILITY_GAP

logger = get_logger(__name__)


def _get_dict(arguments: dict[str, object], key: str) -> dict[str, str] | None:
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
