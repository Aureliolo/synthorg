# module-kind: feature
"""Approval feature manifest.

Declares the approval feature's surface: its state slice (store + gate +
timeout scheduler + review gate), the approvals + review REST controllers,
the approvals MCP domain, and the boot-constructed approval gate. The
approval feature has no dedicated settings namespace.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.approvals.decisions import ApprovalsDecisionsController
from synthorg.api.controllers.approvals.query import ApprovalsQueryController
from synthorg.api.controllers.reviews import ReviewController
from synthorg.approval._construction import wire_construction
from synthorg.approval.state import ApprovalStateSlice
from synthorg.meta.mcp.domains.approvals import APPROVAL_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor


def _approval_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the approvals MCP handler map.

    Returns:
        The approvals ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.approvals import APPROVAL_HANDLERS  # noqa: PLC0415

    return APPROVAL_HANDLERS


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
        mcp_descriptor(
            domain="approvals",
            tool_defs=APPROVAL_TOOLS,
            handlers=_approval_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=("ApprovalGate",),
    depends_on=(),
)
