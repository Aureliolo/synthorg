"""Quota tracking event constants."""

from typing import Final

QUOTA_TRACKER_CREATED: Final[str] = "quota.tracker.created"
QUOTA_USAGE_RECORDED: Final[str] = "quota.usage.recorded"
QUOTA_CHECK_ALLOWED: Final[str] = "quota.check.allowed"
QUOTA_CHECK_DENIED: Final[str] = "quota.check.denied"
QUOTA_DEGRADATION_RESOLUTION_FAILED: Final[str] = "quota.degradation.resolution_failed"
QUOTA_WINDOW_ROTATED: Final[str] = "quota.window.rotated"
QUOTA_SNAPSHOT_QUERIED: Final[str] = "quota.snapshot.queried"
QUOTA_USAGE_SKIPPED: Final[str] = "quota.usage.skipped"
QUOTA_LOOP_AFFINITY_VIOLATED: Final[str] = "quota.loop_affinity.violated"

# Quota poller events
QUOTA_POLL_STARTED: Final[str] = "quota.poll.started"
QUOTA_POLL_COMPLETED: Final[str] = "quota.poll.completed"
QUOTA_POLL_FAILED: Final[str] = "quota.poll.failed"
QUOTA_THRESHOLD_ALERT: Final[str] = "quota.threshold.alert"
QUOTA_ALERT_COOLDOWN_ACTIVE: Final[str] = "quota.alert.cooldown_active"
QUOTA_POLLER_STARTED: Final[str] = "quota.poller.started"
QUOTA_POLLER_STOPPED: Final[str] = "quota.poller.stopped"
