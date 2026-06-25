# module-kind: declarative
"""Persistence event constants for the model_tool_call_signal sub-domain."""

from typing import Final

PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_SAVE_FAILED: Final[str] = (
    "persistence.model_tool_call_signal.save_failed"
)
PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_LOAD_FAILED: Final[str] = (
    "persistence.model_tool_call_signal.load_failed"
)
PERSISTENCE_MODEL_TOOL_CALL_SIGNAL_DELETE_FAILED: Final[str] = (
    "persistence.model_tool_call_signal.delete_failed"
)
