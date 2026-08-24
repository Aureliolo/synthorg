# module-kind: declarative
"""Completion-oracle verdict to task quality score.

"How good was this work" has ONE owner: the completion-oracle reviewer,
which grades a deliverable against its own acceptance criteria at the
``IN_REVIEW`` gate. This module is the deterministic translation of that
verdict into the ``0-10`` figure :class:`TaskMetricRecord` carries, so the
performance ledger reads the judgement rather than reaching a second one.

The verdict picks the band; blocking-severity findings modulate within it.
``ESCALATE`` resolves to ``None`` because no confident verdict was reached,
and an unmeasured score is the honest record of that.
"""

from typing import Final, assert_never

from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReport,
    CompletionOracleVerdict,
)
from synthorg.security.redteam.models import (
    RedTeamSeverity,
    severity_rank,
)

__all__ = [
    "APPROVE_QUALITY_SCORE",
    "APPROVE_WITH_NOTES_QUALITY_SCORE",
    "HIGH_SEVERITY_FINDING_DISCOUNT",
    "MIN_QUALITY_SCORE",
    "REJECT_QUALITY_SCORE",
    "quality_score_for",
]

MIN_QUALITY_SCORE: Final[float] = 0.0
"""Floor of the score range; a discount never takes a record below it."""

APPROVE_QUALITY_SCORE: Final[float] = 10.0
"""Clean approval: the deliverable met every criterion the reviewer checked."""

APPROVE_WITH_NOTES_QUALITY_SCORE: Final[float] = 7.5
"""Approved, but the reviewer attached non-blocking observations."""

REJECT_QUALITY_SCORE: Final[float] = 2.5
"""Sent back as rework: the deliverable did not meet its criteria."""

HIGH_SEVERITY_FINDING_DISCOUNT: Final[float] = 1.0
"""Per-finding discount for a finding at or above :data:`_BLOCKING_SEVERITY`.

Applied per finding rather than once, because two blocking defects in one
deliverable is a worse outcome than one and the band alone cannot say so.
"""

_BLOCKING_SEVERITY: Final[RedTeamSeverity] = RedTeamSeverity.HIGH
"""Severity at and above which a finding discounts the band."""


def _band_for(verdict: CompletionOracleVerdict) -> float | None:
    """Return the score band for *verdict*, or ``None`` when unmeasured.

    Returns:
        The band's base score, or ``None`` for a verdict that reached no
        quality judgement.
    """
    match verdict:
        case CompletionOracleVerdict.APPROVE:
            return APPROVE_QUALITY_SCORE
        case CompletionOracleVerdict.APPROVE_WITH_NOTES:
            return APPROVE_WITH_NOTES_QUALITY_SCORE
        case CompletionOracleVerdict.REJECT:
            return REJECT_QUALITY_SCORE
        case CompletionOracleVerdict.ESCALATE:
            return None
        case unreachable:
            # A match rather than a mapping: a new verdict member is a type
            # error here, where a mapping would answer ``None`` for it and
            # read as "unmeasured" for the life of the release.
            assert_never(unreachable)


def quality_score_for(report: CompletionOracleReport) -> float | None:
    """Translate one filed oracle report into a task quality score.

    Args:
        report: The reviewer's filed report for the deliverable.

    Returns:
        A score in ``[MIN_QUALITY_SCORE, APPROVE_QUALITY_SCORE]``, or
        ``None`` when the review reached no confident verdict.
    """
    band = _band_for(report.verdict)
    if band is None:
        return None
    blocking = sum(
        1
        for finding in report.findings
        if severity_rank(finding.severity) >= severity_rank(_BLOCKING_SEVERITY)
    )
    discounted = band - blocking * HIGH_SEVERITY_FINDING_DISCOUNT
    return max(MIN_QUALITY_SCORE, discounted)
