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
capping their severity at LOW means a noisy stub can never block on
its own. Only the LLM agent's own findings (or, once it ships, a
substrate-backed checker) may escalate to MEDIUM/HIGH/CRITICAL on
the GROUNDING attack surface.
"""

SUBSTRATE_GROUNDING_MAX_SEVERITY: Final[RedTeamSeverity] = RedTeamSeverity.HIGH
"""Maximum severity a substrate-source grounding finding may carry.

The EPIC E #1988 substrate-backed checker resolves claims against a
real corpus; its findings carry more weight than heuristic flags.
Capped at HIGH (not CRITICAL) because even an authoritative grounding
gap is a quality defect, not a security incident.
"""


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

    Takes the merged tuple of agent + heuristic-derived findings so the
    gate's verdict reflects EVERY signal feeding the rework decision,
    not just the agent's report.

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
