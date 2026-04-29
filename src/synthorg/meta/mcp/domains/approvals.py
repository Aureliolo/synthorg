"""Approval domain MCP tools.

``reject`` is destructive -- enforces the ``confirm=True`` + non-blank
``reason`` guardrail at the schema level so a caller who forgets
``confirm`` is rejected by the wire layer before ever reaching the
handler.
"""

from typing import TYPE_CHECKING, get_args

from synthorg.meta.mcp.domains._simple_args import (
    RISK_LEVEL_DEFAULT,
    ApprovalsApproveArgs,
    ApprovalsCreateArgs,
    ApprovalsGetArgs,
    ApprovalsListArgs,
    ApprovalsRejectArgs,
    ApprovalStatus,
    RiskLevel,
)
from synthorg.meta.mcp.tool_builder import (
    DESTRUCTIVE_GUARDRAIL_PROPERTIES,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
    write_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

# Derived from the canonical Literal types in ``_simple_args`` via
# ``typing.get_args`` so the wire schema enum lists cannot drift from
# the args-model surface.  Adding a status / risk level on the args
# side automatically widens the wire enum.
_APPROVAL_STATUS_ENUM = list(get_args(ApprovalStatus))
_RISK_LEVEL_ENUM = list(get_args(RiskLevel))

APPROVAL_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "approvals",
        "list",
        "List approval items with optional filtering.",
        {
            "status": {
                "type": "string",
                "description": "Filter by approval status",
                "enum": _APPROVAL_STATUS_ENUM,
            },
            "risk_level": {
                "type": "string",
                "description": "Filter by risk level",
                "enum": _RISK_LEVEL_ENUM,
            },
            "action_type": {"type": "string", "description": "Filter by action type"},
            **PAGINATION_PROPERTIES,
        },
        args_model=ApprovalsListArgs,
    ),
    read_tool(
        "approvals",
        "get",
        "Get an approval item by ID.",
        {
            "approval_id": {"type": "string", "description": "Approval UUID"},
        },
        required=("approval_id",),
        args_model=ApprovalsGetArgs,
    ),
    write_tool(
        "approvals",
        "create",
        "Create a new approval request.",
        {
            "action_type": {
                "type": "string",
                "description": "Type of action requiring approval",
            },
            "title": {
                "type": "string",
                "description": "Short summary of the approval",
                "minLength": 1,
                "pattern": r".*\S.*",
            },
            "description": {
                "type": "string",
                "description": "Description of the proposed action",
                "minLength": 1,
                "pattern": r".*\S.*",
            },
            "risk_level": {
                "type": "string",
                "description": "Risk level assessment",
                "enum": _RISK_LEVEL_ENUM,
                "default": RISK_LEVEL_DEFAULT,
            },
        },
        required=("action_type", "description"),
        args_model=ApprovalsCreateArgs,
    ),
    write_tool(
        "approvals",
        "approve",
        "Approve a pending approval item.",
        {
            "approval_id": {"type": "string", "description": "Approval UUID"},
            "comment": {"type": "string", "description": "Approval comment"},
        },
        required=("approval_id",),
        args_model=ApprovalsApproveArgs,
    ),
    admin_tool(
        "approvals",
        "reject",
        "Reject a pending approval item (destructive; requires confirm).",
        {
            "approval_id": {"type": "string", "description": "Approval UUID"},
            **DESTRUCTIVE_GUARDRAIL_PROPERTIES,
        },
        required=("approval_id", "reason", "confirm"),
        args_model=ApprovalsRejectArgs,
    ),
)
