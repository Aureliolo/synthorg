# module-kind: declarative
"""Persistence event constants for the dynamic_tool sub-domain.

Failure paths plus read/query markers only: the persistence-boundary gate forbids
repos from emitting their own mutation lifecycle (_SAVED / _DELETED) events; the
toolsmith service layer owns the audit hop.
"""

from typing import Final

PERSISTENCE_DYNAMIC_TOOL_FETCHED: Final[str] = "persistence.dynamic_tool.fetched"
PERSISTENCE_DYNAMIC_TOOL_FETCH_FAILED: Final[str] = (
    "persistence.dynamic_tool.fetch_failed"
)
PERSISTENCE_DYNAMIC_TOOL_LISTED: Final[str] = "persistence.dynamic_tool.listed"
PERSISTENCE_DYNAMIC_TOOL_LIST_FAILED: Final[str] = (
    "persistence.dynamic_tool.list_failed"
)
PERSISTENCE_DYNAMIC_TOOL_QUERIED: Final[str] = "persistence.dynamic_tool.queried"
PERSISTENCE_DYNAMIC_TOOL_QUERY_FAILED: Final[str] = (
    "persistence.dynamic_tool.query_failed"
)
PERSISTENCE_DYNAMIC_TOOL_SAVE_FAILED: Final[str] = (
    "persistence.dynamic_tool.save_failed"
)
PERSISTENCE_DYNAMIC_TOOL_DELETE_FAILED: Final[str] = (
    "persistence.dynamic_tool.delete_failed"
)
PERSISTENCE_DYNAMIC_TOOL_TRANSITION_FAILED: Final[str] = (
    "persistence.dynamic_tool.transition_failed"
)
PERSISTENCE_DYNAMIC_TOOL_DESERIALIZE_FAILED: Final[str] = (
    "persistence.dynamic_tool.deserialize_failed"
)
