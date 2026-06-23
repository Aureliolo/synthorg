"""Shared event-stream-hub accessor for the events controllers.

Extracted from ``_sse.py`` so that module stays within its size budget;
both the SSE machinery and the stream controller resolve the hub through
this single seam.
"""

from synthorg.api.state import AppState
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.domain_errors import NotFoundError


def require_hub(app_state: AppState) -> EventStreamHub:
    """Return the wired event-stream hub or raise when unavailable.

    Raises:
        NotFoundError: When no hub is wired on the communication slice.
    """
    hub = app_state.slice(CommunicationStateSlice).event_stream_hub
    if hub is None:
        msg = "Event stream not configured"
        raise NotFoundError(msg)
    return hub
