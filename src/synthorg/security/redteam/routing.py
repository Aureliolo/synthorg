"""Severity x autonomy verdict computation.

Mirrors the existing :class:`AutonomyTieredPolicy` pattern in
:mod:`synthorg.security.output_scan_policy`: severity tiers map to
blocking outcomes that vary with the operator's autonomy posture.

The matrix (locked decision in plan doc):

+---------+---------------+-----------+----------+----------+
|         | LOCKED        | SUPERVISED| SEMI     | FULL     |
+---------+---------------+-----------+----------+----------+
| CRITICAL| BLOCK         | BLOCK     | BLOCK    | BLOCK    |
| HIGH    | BLOCK         | BLOCK     | BLOCK    | BLOCK    |
| MEDIUM  | BLOCK         | BLOCK     | PASS+    | PASS+    |
| LOW     | PASS+         | PASS+     | PASS+    | PASS+    |
| INFO    | PASS+         | PASS+     | PASS+    | PASS+    |
+---------+---------------+-----------+----------+----------+

(``PASS+`` is shorthand for :data:`RedTeamVerdict.PASS_WITH_FINDINGS`.)

The boundary between blocking and informational severity is named
:data:`SEVERITY_ALWAYS_BLOCK_FROM` (HIGH); the boundary between MEDIUM
blocking under low-autonomy and informational under high-autonomy is
controlled by :data:`AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM`.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.enums import AutonomyLevel
from synthorg.security.redteam.models import (
    RedTeamFinding,
    RedTeamSeverity,
    RedTeamVerdict,
    severity_rank,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

SEVERITY_ALWAYS_BLOCK_FROM: Final[RedTeamSeverity] = RedTeamSeverity.HIGH
"""Severity at and above which the gate ALWAYS blocks, regardless of autonomy."""

SEVERITY_AUTONOMY_DEPENDENT: Final[RedTeamSeverity] = RedTeamSeverity.MEDIUM
"""Severity that blocks only under restrictive autonomy levels."""

AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM: Final[frozenset[AutonomyLevel]] = frozenset(
    {AutonomyLevel.LOCKED, AutonomyLevel.SUPERVISED}
)
"""Autonomy levels under which a MEDIUM finding routes to BLOCK."""

HEURISTIC_GROUNDING_MAX_SEVERITY: Final[RedTeamSeverity] = RedTeamSeverity.LOW
"""Maximum severity a heuristic-source grounding finding may carry.

Heuristic grounding flags are deterministic but not authoritative;
capping their severity at LOW means a noisy heuristic can never block on
its own. Only the LLM agent's own findings or the substrate-backed
checker may escalate to MEDIUM/HIGH/CRITICAL on the GROUNDING attack
surface.
"""

SUBSTRATE_GROUNDING_MAX_SEVERITY: Final[RedTeamSeverity] = RedTeamSeverity.HIGH
"""Maximum severity a substrate-source grounding finding may carry.

The substrate-backed checker resolves claims against a real corpus; its
findings carry more weight than heuristic flags. Capped at HIGH (not
CRITICAL) because even an authoritative grounding gap is a quality
defect, not a security incident.
"""

SUBSTRATE_HIGH_CONFIDENCE_FLOOR: Final[float] = 0.85
"""Ungrounded-confidence at or above which a substrate claim routes to HIGH.

High floor by design: HIGH BLOCKs at every autonomy level, so a
deliverable is only blocked when the checker is strongly confident the
claim is unsupported. This is the precision knob that keeps grounded
work from being wrongly rejected.
"""

SUBSTRATE_MEDIUM_CONFIDENCE_FLOOR: Final[float] = 0.65
"""Ungrounded-confidence at or above which a substrate claim routes to MEDIUM.

MEDIUM BLOCKs only under LOCKED / SUPERVISED autonomy; softer doubts
surface without unconditionally blocking.
"""

SUBSTRATE_LOW_CONFIDENCE_FLOOR: Final[float] = 0.45
"""Ungrounded-confidence at or above which a substrate claim routes to LOW.

Below this floor the claim is dropped entirely: the signal is too weak
to surface even as an informational finding.
"""

SUBSTRATE_DROP_FLOOR: Final[float] = SUBSTRATE_LOW_CONFIDENCE_FLOOR
"""Confidence below which a substrate claim must not be emitted at all.

Enforced by the checker so a claim that would map to no severity never
becomes a finding. Set to match the LOW floor (a value copy bound at
import time, not a live reference); keep the two in sync so the checker's
drop boundary and the routing band edge cannot diverge.
"""


def substrate_severity_for_confidence(confidence: float) -> RedTeamSeverity | None:
    """Map a substrate claim's ungrounded-confidence to a finding severity.

    Banded so the HIGH (blocking) tier needs strong confidence: the top
    band returns :data:`SUBSTRATE_GROUNDING_MAX_SEVERITY` directly, making
    the HIGH cap the single source of truth (an authoritative grounding
    gap is a quality defect, never escalated to CRITICAL).

    Returns:
        The severity tier for ``confidence``, or ``None`` when it falls
        below :data:`SUBSTRATE_DROP_FLOOR` and the claim should not become
        a finding.
    """
    if confidence >= SUBSTRATE_HIGH_CONFIDENCE_FLOOR:
        return SUBSTRATE_GROUNDING_MAX_SEVERITY
    if confidence >= SUBSTRATE_MEDIUM_CONFIDENCE_FLOOR:
        return RedTeamSeverity.MEDIUM
    if confidence >= SUBSTRATE_LOW_CONFIDENCE_FLOOR:
        return RedTeamSeverity.LOW
    return None


def should_block(severity: RedTeamSeverity, autonomy: AutonomyLevel) -> bool:
    """Return whether a single finding of ``severity`` blocks at ``autonomy``.

    The decision boundary is fully data-driven by
    :data:`SEVERITY_ALWAYS_BLOCK_FROM`,
    :data:`SEVERITY_AUTONOMY_DEPENDENT`, and
    :data:`AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM` so the matrix in the
    module docstring is the only spec a reader needs.
    """
    if severity_rank(severity) >= severity_rank(SEVERITY_ALWAYS_BLOCK_FROM):
        return True
    if severity is SEVERITY_AUTONOMY_DEPENDENT:
        return autonomy in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM
    return False


def compute_red_team_verdict(
    findings: Sequence[RedTeamFinding],
    autonomy: AutonomyLevel,
) -> RedTeamVerdict:
    """Aggregate verdict for a flat sequence of findings under ``autonomy``.

    Takes the merged tuple of agent + grounding findings so the gate's
    verdict reflects EVERY signal feeding the rework decision, not just
    the agent's report.

    * Empty: :data:`RedTeamVerdict.PASS`.
    * Any finding triggers :func:`should_block`: :data:`RedTeamVerdict.BLOCK`.
    * Otherwise: :data:`RedTeamVerdict.PASS_WITH_FINDINGS`.

    Returns:
        The aggregate :class:`RedTeamVerdict`.
    """
    if not findings:
        return RedTeamVerdict.PASS
    for finding in findings:
        if should_block(finding.severity, autonomy):
            return RedTeamVerdict.BLOCK
    return RedTeamVerdict.PASS_WITH_FINDINGS
