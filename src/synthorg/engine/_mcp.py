# module-kind: code
"""Engine feature MCP descriptors (tasks + workflows domains).

Kept beside ``engine/feature.py`` so the manifest stays under the
feature-tier line cap: the engine feature carries a large ghost-wired
symbol set, so its MCP descriptors + deferred handler loaders live here
and the manifest imports the assembled tuple by one name.
"""

from collections.abc import Mapping

from synthorg.meta.mcp.domains.tasks import TASK_TOOLS
from synthorg.meta.mcp.domains.workflows import WORKFLOW_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor


def _task_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the tasks MCP handler map.

    Returns:
        The tasks ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.tasks import TASK_HANDLERS  # noqa: PLC0415

    return TASK_HANDLERS


def _workflow_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the workflows MCP handler map.

    Returns:
        The workflows ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.workflows import WORKFLOW_HANDLERS  # noqa: PLC0415

    return WORKFLOW_HANDLERS


ENGINE_MCP_HANDLERS = (
    mcp_descriptor(
        domain="tasks",
        tool_defs=TASK_TOOLS,
        handlers=_task_mcp_handlers,
    ),
    mcp_descriptor(
        domain="workflows",
        tool_defs=WORKFLOW_TOOLS,
        handlers=_workflow_mcp_handlers,
    ),
)
