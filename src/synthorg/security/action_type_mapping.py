"""Default mapping from ToolCategory to ActionType.

Provides the ``DEFAULT_CATEGORY_ACTION_MAP`` used by ``BaseTool`` to
derive a default ``action_type`` from its ``category`` when none is
explicitly set.  Categories not present in the map fall back to
``ActionType.CODE_READ`` at the point of use in ``BaseTool``.
"""

from types import MappingProxyType
from typing import Final

from synthorg.core.enums import ActionType, ToolCategory

DEFAULT_CATEGORY_ACTION_MAP: Final[MappingProxyType[ToolCategory, ActionType]] = (
    MappingProxyType(
        {
            ToolCategory.FILE_SYSTEM: ActionType.CODE_WRITE,
            ToolCategory.CODE_EXECUTION: ActionType.CODE_WRITE,
            ToolCategory.VERSION_CONTROL: ActionType.VCS_COMMIT,
            ToolCategory.WEB: ActionType.COMMS_EXTERNAL,
            ToolCategory.DATABASE: ActionType.DB_QUERY,
            ToolCategory.TERMINAL: ActionType.CODE_WRITE,
            ToolCategory.DESIGN: ActionType.DOCS_WRITE,
            ToolCategory.COMMUNICATION: ActionType.COMMS_INTERNAL,
            ToolCategory.ANALYTICS: ActionType.CODE_READ,
            ToolCategory.DEPLOYMENT: ActionType.DEPLOY_STAGING,
            ToolCategory.MEMORY: ActionType.MEMORY_READ,
            ToolCategory.ONTOLOGY: ActionType.MEMORY_READ,
            ToolCategory.MCP: ActionType.CODE_WRITE,
            ToolCategory.BROWSER: ActionType.BROWSER_NAVIGATE,
            ToolCategory.EXTERNAL_DATA: ActionType.EXTERNAL_DATA_REQUEST,
            ToolCategory.DESKTOP: ActionType.DESKTOP_LAUNCH,
            ToolCategory.OTHER: ActionType.CODE_READ,
        }
    )
)
