"""Tools feature state slice.

Holds the tool-invocation tracker (records per-tool usage for the
activities feed), the tool-execution capability report, and which web
research tools the runtime actually installed. All are ``None`` until
wired; the activities controller guards on the tracker's absence, and
the report's absence IS the ``agent_tool_execution`` subsystem's
liveness answer, so nothing installs one it cannot stand behind.
"""

from typing import TYPE_CHECKING

from pydantic import AwareDatetime, BaseModel, ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.tools.invocation_tracker import (
    ToolInvocationTracker,
)
from synthorg.tools.sandbox.execution_capability import ToolExecutionCapability
from synthorg.tools.sandbox.reclaim import SandboxReclaimScheduler


class WebResearchTools(BaseModel):
    """Which web research tools the last runtime assembly installed.

    Settings say what an operator ASKED for; this says what an agent can
    actually call. The two diverge whenever runtime assembly stops before it
    reaches the tool registry, which it does with no active provider and with
    an unbound decomposition pair. Reporting the request as the capability
    told an operator that web research was live while no session held either
    tool.

    Attributes:
        search: Whether ``web_search`` reached the tool registry.
        fetch: Whether ``web_fetch`` reached it, with at least one rung.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    search: bool = False
    fetch: bool = False


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
    sandbox_reconciled_at: AwareDatetime | None = None
    # The sweep that releases a reusable container once its owner's run has
    # finished; its presence IS the subsystem's liveness answer.
    sandbox_reclaim_scheduler: SandboxReclaimScheduler | None = None
    # ``None`` until a runtime assembly completes, which is the honest answer
    # before one has: not "no web tools", but "nothing has built any yet".
    web_research: WebResearchTools | None = None


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
