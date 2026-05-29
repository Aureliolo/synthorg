# module-kind: feature
"""Approval feature manifest.

Declares the approval feature's surface: its state slice (store + gate +
timeout scheduler + review gate), the approvals + review REST controllers,
the MCP tools, and the boot-constructed approval gate. The approval feature
has no dedicated settings namespace.
"""

from synthorg._core.features import (
    FeatureManifest,
    FeatureModule,
    McpHandlerDescriptor,
)
from synthorg.api.controllers.approvals.decisions import ApprovalsDecisionsController
from synthorg.api.controllers.approvals.query import ApprovalsQueryController
from synthorg.api.controllers.reviews import ReviewController
from synthorg.approval.state import ApprovalStateSlice
from synthorg.meta.mcp.domains.approvals import APPROVAL_TOOLS

FEATURE: FeatureModule = FeatureManifest(
    name="approval",
    settings_namespace=None,
    state_slice=ApprovalStateSlice,
    controllers=(
        ApprovalsQueryController,
        ApprovalsDecisionsController,
        ReviewController,
    ),
    mcp_handlers=(
        McpHandlerDescriptor(
            domain="approvals",
            tool_names=tuple(tool.name for tool in APPROVAL_TOOLS),
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=("ApprovalGate",),
    depends_on=(),
)
