"""SHIP-time retrospective capture event constants."""

from typing import Final

# The retrospective capture lifecycle, fired from the initiative rollup when a
# project reaches COMPLETED.
RETRO_CAPTURE_STARTED: Final[str] = "retrospective.capture.started"
RETRO_CAPTURE_COMPLETED: Final[str] = "retrospective.capture.completed"
RETRO_CAPTURE_SKIPPED: Final[str] = "retrospective.capture.skipped"
RETRO_CAPTURE_FAILED: Final[str] = "retrospective.capture.failed"
#: A capture that distilled learnings but persisted none of them (a total
#: write-side outage), distinct from a genuinely empty retrospective.
RETRO_CAPTURE_WRITE_INCOMPLETE: Final[str] = "retrospective.capture.write_incomplete"
#: A best-effort settings read degraded to its default, so an operator's
#: change is silently not in effect until the read recovers.
RETRO_CAPTURE_SETTINGS_DEGRADED: Final[str] = "retrospective.capture.settings_degraded"

# The owner-run distillation session.
RETRO_SESSION_STARTED: Final[str] = "retrospective.session.started"
RETRO_SESSION_COMPLETED: Final[str] = "retrospective.session.completed"
RETRO_SESSION_NO_DRAFT: Final[str] = "retrospective.session.no_draft"
RETRO_SESSION_DUPLICATE_SUBMIT: Final[str] = "retrospective.session.duplicate_submit"
#: A submitted retrospective failed to parse; the lead is asked to resubmit
#: within the session.
RETRO_SESSION_SUBMIT_REJECTED: Final[str] = "retrospective.session.submit_rejected"

# The write side: learnings landing in org and agent memory.
RETRO_ORG_LEARNING_WRITTEN: Final[str] = "retrospective.org_learning.written"
RETRO_AGENT_LEARNING_WRITTEN: Final[str] = "retrospective.agent_learning.written"
RETRO_WRITE_FAILED: Final[str] = "retrospective.write.failed"
