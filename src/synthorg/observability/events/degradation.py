"""Quota degradation event constants."""

from typing import Final

DEGRADATION_QUEUE_STARTED: Final[str] = "degradation.queue.started"
DEGRADATION_QUEUE_WAITING: Final[str] = "degradation.queue.waiting"
DEGRADATION_QUEUE_RESUMED: Final[str] = "degradation.queue.resumed"
DEGRADATION_QUEUE_EXHAUSTED: Final[str] = "degradation.queue.exhausted"
DEGRADATION_QUEUE_WINDOW_ROTATED: Final[str] = "degradation.queue.window_rotated"
DEGRADATION_ALERT_RAISED: Final[str] = "degradation.alert.raised"
