# module-kind: code
"""Publishing whether this deployment can execute an agent tool at all.

The probes live in :mod:`synthorg.tools.sandbox.execution_capability`; this is
the wiring that turns their report into something ``GET /subsystems`` can
answer with. A deployment that can plan and review but cannot spawn a process
or reach a container is a real and reachable state, and unannounced it is
indistinguishable from a model failure: the only place the condition surfaces
is one error per tool call, many turns into a run.

The capability is installed only when both halves are there, so liveness reads
from what the activation actually established rather than from the fact that it
ran. A decline names its own condition, which is what keeps
``check_subsystem_decline_reason.py`` satisfied without a settings declaration:
the condition here is not a blank setting, it is the platform.

What ``active`` means, precisely: the tool plane came up. The reconciler leaves
an already-active subsystem alone, so a daemon that dies later is not
re-detected here and surfaces at the next tool call instead. The direction that
matters is the other one, and it does work: a subsystem blocked because Docker
was not running yet is re-attempted on the periodic sweep, so starting Docker
brings it up without restarting the backend.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.tools.sandbox.execution_capability import probe_tool_execution
from synthorg.tools.state import ToolsStateSlice

logger = get_logger(__name__)


async def wire_tool_execution_capability(app_state: AppState) -> None:
    """Probe the tool plane and publish the result, or decline naming why.

    Args:
        app_state: Application state carrying the workspace root and the tools
            slice the report is published on.

    Raises:
        SubsystemDeclinedError: This process cannot spawn a subprocess, cannot
            reach the container backend, or cannot describe its workspace to
            it. The reason names the tools each condition costs, because
            "which tools stop working" is what an operator acts on and the
            underlying condition alone is not.
    """
    capability = await probe_tool_execution(
        workspace=agent_workspace_root_of(app_state)
    )
    reason = capability.decline_reason
    if reason is not None:
        raise SubsystemDeclinedError(reason)
    app_state.wire(ToolsStateSlice, tool_execution=capability)
    mount = capability.workspace_mount
    logger.info(
        API_APP_STARTUP,
        service="agent_tool_execution",
        note="wired",
        workspace_volume=None if mount is None else mount.volume,
        workspace_subpath=None if mount is None else mount.subpath,
    )


__all__ = ["wire_tool_execution_capability"]
