"""Runtime tool-call feedback state slice.

Holds the installed :class:`ToolCallFeedbackTracker`. ``None`` until wired
at boot; the boot hook skips wiring when persistence or the provider
management service is absent, and the re-enable controller 503s when the
tracker is unwired. Behaviour (whether observations actually downgrade a
model) is gated live by the ``providers.tool_call_feedback_enabled``
setting inside the tracker, so the sink can stay installed while the
feature is toggled off.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.providers.tool_call_feedback.tracker import ToolCallFeedbackTracker


class ToolCallFeedbackStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the runtime tool-call feedback loop."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tracker: ToolCallFeedbackTracker | None = None


def tool_call_feedback_tracker_of(
    app_state: AppStateSliceMixin,
) -> ToolCallFeedbackTracker:
    """Return the wired tool-call feedback tracker, or raise 503.

    The manual "re-enable tool calling" endpoint resolves the tracker
    through this accessor; an unwired tracker (persistence or management
    absent at boot) surfaces a clean ``ServiceUnavailableError``.

    Args:
        app_state: The application state (any slice-reader).

    Returns:
        The wired tracker.

    Raises:
        ServiceUnavailableError: When the tracker is not yet wired.
    """
    return require_service(
        app_state.slice(ToolCallFeedbackStateSlice).tracker,
        "Tool-Call Feedback",
    )
