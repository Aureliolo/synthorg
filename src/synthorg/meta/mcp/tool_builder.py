"""MCP tool definition builder helpers.

Provides concise builder functions for creating ``MCPToolDef`` instances.
Each builder sets the capability tag automatically from domain and action
type, enforcing the ``synthorg_{domain}_{action}`` naming convention.
"""

from typing import Any

from pydantic import BaseModel  # noqa: TC002 -- Pydantic field type at runtime

from synthorg.meta.mcp.registry import MCPToolDef
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_TOOL_BUILDER_INVALID_REQUIRED

logger = get_logger(__name__)

PAGINATION_PROPERTIES: dict[str, Any] = {
    "offset": {
        "type": "integer",
        "description": "Pagination offset",
        "default": 0,
        "minimum": 0,
    },
    "limit": {
        "type": "integer",
        "description": "Page size",
        "default": 50,
        "minimum": 1,
        "maximum": 1000,
    },
}
"""Shared pagination schema with bounds for all domain list tools."""


ADMIN_GUARDRAIL_PROPERTIES: dict[str, Any] = {
    "reason": {
        "type": "string",
        "description": "Reason for the admin action (non-blank)",
        "minLength": 1,
        "pattern": r".*\S.*",
    },
    "confirm": {
        "type": "boolean",
        "description": "Must be true to proceed",
        "enum": [True],
    },
}
"""Shared ``reason`` + ``confirm`` schema for every ``admin_tool``.

Every admin tool's schema spreads ``**ADMIN_GUARDRAIL_PROPERTIES``
into its ``properties`` dict and lists ``"reason"`` + ``"confirm"`` in
``required``, so the wire-level contract (non-whitespace reason, literal
``True`` confirm) stays uniform across every admin op and matches the
``require_admin_guardrails()`` handler-side check exactly.
"""


ADMIN_GUARDRAIL_REQUIRED: tuple[str, ...] = ("reason", "confirm")
"""Required-field names that accompany :data:`ADMIN_GUARDRAIL_PROPERTIES`."""


def _make_parameters(
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a JSON Schema ``object`` for tool parameters.

    Args:
        properties: Property definitions (``None`` for empty).
        required: Required property names.

    Returns:
        JSON Schema dict.
    """
    resolved = properties or {}
    if required:
        unknown = set(required) - set(resolved)
        if unknown:
            msg = (
                f"required keys {sorted(unknown)} not declared "
                f"in properties {sorted(resolved)}"
            )
            logger.warning(
                MCP_TOOL_BUILDER_INVALID_REQUIRED,
                unknown_keys=sorted(unknown),
                properties=sorted(resolved),
            )
            raise ValueError(msg)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": resolved,
    }
    if required:
        schema["required"] = list(required)
    return schema


def tool_def(  # noqa: PLR0913
    domain: str,
    action: str,
    description: str,
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    capability_action: str = "read",
    args_model: type[BaseModel] | None = None,
) -> MCPToolDef:
    """Build an MCP tool definition with naming convention enforcement.

    Args:
        domain: Domain slug (e.g. ``"tasks"``).
        action: Action slug (e.g. ``"list"``, ``"create"``).
        description: Human-readable tool description.
        properties: JSON Schema properties for parameters.
        required: Required parameter names.
        capability_action: Capability action tag (``"read"``, ``"write"``,
            or ``"admin"``).
        args_model: Optional Pydantic args model for typed validation at
            the invoker boundary; the JSON-Schema parameters are still
            built from ``properties``/``required`` for the
            ``tools/list`` wire surface.

    Returns:
        Frozen ``MCPToolDef`` instance.
    """
    name = f"synthorg_{domain}_{action}"
    capability = f"{domain}:{capability_action}"
    handler_key = name
    return MCPToolDef(
        name=name,
        description=description,
        parameters=_make_parameters(properties, required=required),
        capability=capability,
        handler_key=handler_key,
        args_model=args_model,
    )


def read_tool(  # noqa: PLR0913
    domain: str,
    action: str,
    description: str,
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    args_model: type[BaseModel] | None = None,
) -> MCPToolDef:
    """Build a read-only MCP tool definition.

    Shorthand for ``tool_def(..., capability_action="read")``.

    Args:
        domain: Domain slug.
        action: Action slug.
        description: Human-readable description.
        properties: JSON Schema properties.
        required: Required parameter names.
        args_model: Optional Pydantic args model for typed validation.

    Returns:
        Frozen ``MCPToolDef`` with ``read`` capability.
    """
    return tool_def(
        domain,
        action,
        description,
        properties,
        required=required,
        capability_action="read",
        args_model=args_model,
    )


def write_tool(  # noqa: PLR0913
    domain: str,
    action: str,
    description: str,
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    args_model: type[BaseModel] | None = None,
) -> MCPToolDef:
    """Build a write MCP tool definition.

    Shorthand for ``tool_def(..., capability_action="write")``.

    Args:
        domain: Domain slug.
        action: Action slug.
        description: Human-readable description.
        properties: JSON Schema properties.
        required: Required parameter names.
        args_model: Optional Pydantic args model for typed validation.

    Returns:
        Frozen ``MCPToolDef`` with ``write`` capability.
    """
    return tool_def(
        domain,
        action,
        description,
        properties,
        required=required,
        capability_action="write",
        args_model=args_model,
    )


def admin_tool(  # noqa: PLR0913
    domain: str,
    action: str,
    description: str,
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    args_model: type[BaseModel] | None = None,
) -> MCPToolDef:
    """Build an admin MCP tool definition.

    Shorthand for ``tool_def(..., capability_action="admin")``.

    Args:
        domain: Domain slug.
        action: Action slug.
        description: Human-readable description.
        properties: JSON Schema properties.
        required: Required parameter names.
        args_model: Optional Pydantic args model for typed validation.

    Returns:
        Frozen ``MCPToolDef`` with ``admin`` capability.
    """
    return tool_def(
        domain,
        action,
        description,
        properties,
        required=required,
        capability_action="admin",
        args_model=args_model,
    )
