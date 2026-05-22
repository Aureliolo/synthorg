"""Project charter domain MCP tools.

Operator surface over the deep CEO interview to project charter flow:
run interview turns, list / inspect charters, and approve (admin;
spends budget + runs the spine) or cancel them. ``approve`` enforces
the ``confirm=True`` + non-blank ``reason`` guardrail at the schema
level.
"""

from typing import TYPE_CHECKING, get_args

from synthorg.meta.mcp.domains._charter_args import (
    CharterApproveArgs,
    CharterCancelArgs,
    CharterGetArgs,
    CharterInterviewArgs,
    CharterListArgs,
    CharterStatusLiteral,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
    write_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

_CHARTER_STATUS_ENUM = list(get_args(CharterStatusLiteral))

CHARTER_TOOLS: tuple[MCPToolDef, ...] = (
    write_tool(
        "charter",
        "interview",
        "Run one deep CEO interview turn: a question, or a drafted charter.",
        {
            "message": {
                "type": "string",
                "description": "The human's message this turn",
                "minLength": 1,
                "pattern": r".*\S.*",
            },
            "conversation_id": {
                "type": "string",
                "description": "Existing interview to continue, or omit to open one",
            },
            "project": {
                "type": "string",
                "description": "Existing project id to target (else propose a new one)",
            },
        },
        required=("message",),
        args_model=CharterInterviewArgs,
    ),
    read_tool(
        "charter",
        "list",
        "List project charters with optional filtering.",
        {
            "status": {
                "type": "string",
                "description": "Filter by charter status",
                "enum": _CHARTER_STATUS_ENUM,
            },
            "project_id": {"type": "string", "description": "Filter by project id"},
            "created_by": {
                "type": "string",
                "description": "Filter by interview owner",
            },
            **PAGINATION_PROPERTIES,
        },
        args_model=CharterListArgs,
    ),
    read_tool(
        "charter",
        "get",
        "Get a project charter by id.",
        {
            "charter_id": {"type": "string", "description": "Charter id"},
        },
        required=("charter_id",),
        args_model=CharterGetArgs,
    ),
    write_tool(
        "charter",
        "cancel",
        "Cancel a DRAFTED charter (terminal).",
        {
            "charter_id": {"type": "string", "description": "Charter id"},
        },
        required=("charter_id",),
        args_model=CharterCancelArgs,
    ),
    admin_tool(
        "charter",
        "approve",
        "Approve a charter and dispatch its project run (admin; spends budget).",
        {
            "charter_id": {"type": "string", "description": "Charter id"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("charter_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=CharterApproveArgs,
    ),
)

__all__ = ["CHARTER_TOOLS"]
