# module-kind: code
"""Boot wiring for the runtime tool-call failure feedback loop.

Builds the :class:`ToolCallFeedbackTracker` and installs it as the global
tool-call signal sink so the provider boundary
(``BaseCompletionProvider``) routes tool-call outcomes into it. Wired
whenever a provider management service is built AND persistence is
connected; the ``providers.tool_call_feedback_enabled`` setting is NOT a
boot gate -- the tracker re-reads it live per observation, so an operator
can toggle the feature on/off without a restart while the cheap sink
stays installed.

Best-effort and idempotent: returns early when already wired (silently),
or when the settings resolver / management service / persistence backend
is absent (logging an ``API_APP_STARTUP`` warning each time so a
settings-wiring failure in production is never an invisible skip), so a
transient minimal-app boot cannot poison startup. The sink is installed
LAST, after the state slice is published, so a partial build leaves no
dangling sink.
"""

from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.state import ProvidersStateSlice
from synthorg.providers.tool_call_feedback.sink import install_tool_call_signal_sink
from synthorg.providers.tool_call_feedback.state import ToolCallFeedbackStateSlice
from synthorg.providers.tool_call_feedback.tracker import ToolCallFeedbackTracker
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


async def wire_tool_call_feedback(app_state: AppState) -> None:
    """Wire the tool-call feedback tracker + global sink at startup.

    Idempotent for re-entered lifespans (shared-app fixtures): returns
    early when a tracker is already wired.
    """
    if app_state.slice(ToolCallFeedbackStateSlice).tracker is not None:
        return
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        logger.warning(
            API_APP_STARTUP,
            service="tool_call_feedback",
            note="settings resolver absent; feedback disabled",
        )
        return
    management = app_state.slice(ProvidersStateSlice).management
    backend = app_state.slice(PersistenceStateSlice).backend
    if management is None or backend is None:
        logger.warning(
            API_APP_STARTUP,
            service="tool_call_feedback",
            note="management or persistence absent; feedback disabled",
        )
        return

    tracker = ToolCallFeedbackTracker(
        repo=backend.model_tool_call_signals,
        writer=management,
        settings=resolver,
    )
    app_state.swap_slice(ToolCallFeedbackStateSlice(tracker=tracker))
    # Install the sink LAST so a failed build never leaves the provider
    # boundary routing into a half-wired tracker.
    install_tool_call_signal_sink(tracker)
    logger.info(API_APP_STARTUP, service="tool_call_feedback", note="wired")


__all__ = ["wire_tool_call_feedback"]
