"""Runtime tool-call failure feedback for the provider boundary.

Detects repeated tool-call failures per ``(provider, model)`` at the
``BaseCompletionProvider`` boundary (a non-retryable rejection of a
tools-bearing request, or a malformed tool-use response with no tool
calls), accumulates a time-decayed failure score, and downgrades a model's
persisted ``ModelMetadata.tool_calls_verified`` to ``False`` once the
score crosses the configured threshold so the matcher stops assigning it
to tool-requiring agents. A genuine tool-call success clears the signal
and re-enables a downgraded model.

The provider boundary imports only :mod:`.sink` (a lightweight global
emit seam), so it never depends on the management/persistence layer; the
:class:`.tracker.ToolCallFeedbackTracker` is installed as the sink at
boot (``api.lifecycle_helpers.tool_call_feedback_wiring``).
"""

from synthorg.providers.tool_call_feedback.sink import (
    ToolCallOutcome,
    ToolCallSignalSink,
    emit_tool_call_outcome,
    get_tool_call_signal_sink,
    install_tool_call_signal_sink,
    uninstall_tool_call_signal_sink,
)
from synthorg.providers.tool_call_feedback.tracker import (
    ToolCallCapabilityWriter,
    ToolCallFeedbackSettings,
    ToolCallFeedbackTracker,
)

__all__ = [
    "ToolCallCapabilityWriter",
    "ToolCallFeedbackSettings",
    "ToolCallFeedbackTracker",
    "ToolCallOutcome",
    "ToolCallSignalSink",
    "emit_tool_call_outcome",
    "get_tool_call_signal_sink",
    "install_tool_call_signal_sink",
    "uninstall_tool_call_signal_sink",
]
