"""Event stream observability event constants."""

from typing import Final

EVENT_STREAM_CLIENT_CONNECTED: Final[str] = "event_stream.client.connected"
EVENT_STREAM_CLIENT_DISCONNECTED: Final[str] = "event_stream.client.disconnected"
EVENT_STREAM_EVENT_PROJECTED: Final[str] = "event_stream.event.projected"
EVENT_STREAM_PROJECTION_FAILED: Final[str] = "event_stream.event.projection_failed"
EVENT_STREAM_INTERRUPT_CREATED: Final[str] = "event_stream.interrupt.created"
EVENT_STREAM_INTERRUPT_RESUMED: Final[str] = "event_stream.interrupt.resumed"
EVENT_STREAM_INTERRUPT_EXPIRED: Final[str] = "event_stream.interrupt.expired"
EVENT_STREAM_INTERRUPT_NOT_FOUND: Final[str] = "event_stream.interrupt.not_found"
EVENT_STREAM_INTERRUPT_DUPLICATE: Final[str] = "event_stream.interrupt.duplicate"
EVENT_STREAM_INVALID_RESUME_PAYLOAD: Final[str] = (
    "event_stream.interrupt.invalid_resume_payload"
)
EVENT_STREAM_HUB_STARTED: Final[str] = "event_stream.hub.started"
EVENT_STREAM_HUB_STOPPED: Final[str] = "event_stream.hub.stopped"
EVENT_STREAM_HUB_STOP_TIMEOUT: Final[str] = "event_stream.hub.stop_timeout"
EVENT_STREAM_HUB_PUBLISH_FAILED: Final[str] = "event_stream.hub.publish_failed"
EVENT_STREAM_HUB_PUBLISH_DEDUPED: Final[str] = "event_stream.hub.publish_deduped"
EVENT_STREAM_HUB_JANITOR_PRUNED: Final[str] = "event_stream.hub.janitor_pruned"
