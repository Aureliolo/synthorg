"""Tools feature state slice.

Holds the tool-invocation tracker (records per-tool usage for the
activities feed) and the tool-execution capability report. Both are
``None`` until wired; the activities controller guards on the tracker's
absence, and the report's absence IS the ``agent_tool_execution``
subsystem's liveness answer, so nothing installs one it cannot stand
behind.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.tools.invocation_tracker import (
    ToolInvocationTracker,
)
from synthorg.tools.sandbox.execution_capability import ToolExecutionCapability

if TYPE_CHECKING:
    # ``api.state_slices`` is kept under TYPE_CHECKING: the ``api`` layer wires
    # this feature slice, so a runtime import back up into ``api`` closes a
    # circular import. PEP 649 makes the bare annotation below safe at load.
    from synthorg.api.state_slices import AppStateSliceMixin


class ToolsStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the tools feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    invocation_tracker: ToolInvocationTracker | None = None
    tool_execution: ToolExecutionCapability | None = None


def tool_invocation_tracker_of(
    app_state: AppStateSliceMixin,
) -> ToolInvocationTracker:
    """Resolve the tool-invocation tracker from its slice, or raise 503.

    Returns:
        The wired tool-invocation tracker.
    """
    return require_service(
        app_state.slice(ToolsStateSlice).invocation_tracker,
        "Tool Invocation Tracker",
    )
