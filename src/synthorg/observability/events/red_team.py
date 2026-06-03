"""Observability event constants for the adversarial red-team gate.

Naming pattern follows the existing ``approval_gate`` and
``review_pipeline`` modules: ``<domain>.<noun>.<verb>``.
"""

from typing import Final

RED_TEAM_GATE_STARTED: Final[str] = "red_team.gate.started"
"""Gate began evaluating a deliverable."""

RED_TEAM_GATE_SKIPPED: Final[str] = "red_team.gate.skipped"
"""Gate skipped (feature flag off, or no provider configured)."""

RED_TEAM_GATE_BUILD_FAILED: Final[str] = "red_team.gate.build_failed"
"""Gate enabled but a boot-time build precondition failed (raises)."""

RED_TEAM_AGENT_INVOKED: Final[str] = "red_team.agent.invoked"
"""Gate dispatched the red-team agent for inline evaluation."""

RED_TEAM_AGENT_FAILED: Final[str] = "red_team.agent.failed"
"""Red-team agent dispatch or run raised; fail-OPEN policy applied."""

RED_TEAM_REPORT_RECEIVED: Final[str] = "red_team.report.received"
"""Agent filed a report via the ``submit_red_team_report`` tool."""

RED_TEAM_REPORT_EXECUTION_ID_MISMATCH: Final[str] = (
    "red_team.report.execution_id_mismatch"
)
"""Stored report's execution_id differs from the gate's input; corrected on the fly."""

RED_TEAM_REPORT_MISSING: Final[str] = "red_team.report.missing"
"""Agent did not file a report; gate synthesises an INFO-severity finding."""

RED_TEAM_REPORT_VALIDATION_FAILED: Final[str] = "red_team.report.validation_failed"
"""submit_red_team_report payload failed schema validation."""

RED_TEAM_FINDING_FILED: Final[str] = "red_team.finding.filed"
"""One finding from a report (emitted per finding for fan-out logs)."""

RED_TEAM_GROUNDING_CHECK_STARTED: Final[str] = "red_team.grounding.started"
"""Grounding stub started scanning a deliverable."""

RED_TEAM_GROUNDING_CHECK_COMPLETED: Final[str] = "red_team.grounding.completed"
"""Grounding stub returned (with the count of ungrounded claims)."""

RED_TEAM_GROUNDING_CHECK_FAILED: Final[str] = "red_team.grounding.failed"
"""Grounding stub raised; the gate proceeds without grounding findings."""

RED_TEAM_GATE_PASSED: Final[str] = "red_team.gate.passed"
"""Gate verdict was PASS or PASS_WITH_FINDINGS; deliverable proceeds."""

RED_TEAM_GATE_BLOCKED: Final[str] = "red_team.gate.blocked"
"""Gate verdict was BLOCK; deliverable will be routed back as rework."""

RED_TEAM_REWORK_ROUTED: Final[str] = "red_team.rework.routed"
"""Review gate consumed the BLOCK verdict and routed task to IN_PROGRESS."""

RED_TEAM_GATE_DISPATCHED: Final[str] = "red_team.gate.dispatched"
"""Completion gate dispatched as a tracked background task off the approve path."""

RED_TEAM_NO_DELIVERABLE: Final[str] = "red_team.gate.no_deliverable"
"""No reviewable deliverable could be built for a completing task."""

RED_TEAM_REPORT_ARCHIVED: Final[str] = "red_team.report.archived"
"""Merged report + verdict persisted to the durable cross-process archive."""

RED_TEAM_REPORT_ARCHIVE_FAILED: Final[str] = "red_team.report.archive_failed"
"""Durable archive write failed; the gate verdict stands (fail-OPEN audit)."""

RED_TEAM_REPORT_ALREADY_ARCHIVED: Final[str] = "red_team.report.already_archived"
"""A report for this execution was already archived; the write is a no-op."""

RED_TEAM_REPORT_SAVE_FAILED: Final[str] = "red_team.report.save_failed"
"""Archive repository failed to persist a report record."""

RED_TEAM_REPORT_QUERY_FAILED: Final[str] = "red_team.report.query_failed"
"""Archive repository failed to read report records."""

RED_TEAM_REPORT_DELETE_FAILED: Final[str] = "red_team.report.delete_failed"
"""Archive repository failed to purge report records before a threshold."""

RED_TEAM_REPORT_DESERIALIZE_FAILED: Final[str] = "red_team.report.deserialize_failed"
"""A stored report row could not be decoded back into a record."""
