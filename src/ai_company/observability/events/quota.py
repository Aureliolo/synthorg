"""Quota tracking event constants."""

from typing import Final

QUOTA_TRACKER_CREATED: Final[str] = "quota.tracker.created"
QUOTA_USAGE_RECORDED: Final[str] = "quota.usage.recorded"
QUOTA_CHECK_ALLOWED: Final[str] = "quota.check.allowed"
QUOTA_CHECK_DENIED: Final[str] = "quota.check.denied"
QUOTA_WINDOW_ROTATED: Final[str] = "quota.window.rotated"
QUOTA_SNAPSHOT_QUERIED: Final[str] = "quota.snapshot.queried"
QUOTA_USAGE_SKIPPED: Final[str] = "quota.usage.skipped"
