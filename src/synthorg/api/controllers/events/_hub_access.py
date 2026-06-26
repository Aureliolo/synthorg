"""Shared event-stream-hub accessor for the events controllers.

Extracted from ``_sse.py`` so that module stays within its size budget;
both the SSE machinery and the stream controller resolve the hub through
this single seam.
"""

from synthorg.api.state import AppState
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.domain_errors import ServiceUnavailableError


def require_hub(app_state: AppState) -> EventStreamHub:
    """Return the wired event-stream hub or raise when unavailable.

    An unwired hub is a deployment / service-availability problem, not a
    missing resource, so it surfaces as a retryable 503 with a generic
    message rather than a 404 that would leak the hub's configuration
    state to any authenticated caller.

    Raises:
        ServiceUnavailableError: When no hub is wired on the
            communication slice.
    """
    hub = app_state.slice(CommunicationStateSlice).event_stream_hub
    if hub is None:
        msg = "Event stream unavailable"
        raise ServiceUnavailableError(msg)
    return hub
