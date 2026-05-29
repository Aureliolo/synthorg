"""Severity x autonomy verdict computation for vision findings.

Mirrors the red-team routing matrix: severity tiers map to blocking
outcomes that vary with the operator's autonomy posture.

+---------+---------------+-----------+----------+----------+
|         | LOCKED        | SUPERVISED| SEMI     | FULL     |
+---------+---------------+-----------+----------+----------+
| CRITICAL| BLOCK         | BLOCK     | BLOCK    | BLOCK    |
| HIGH    | BLOCK         | BLOCK     | BLOCK    | BLOCK    |
| MEDIUM  | BLOCK         | BLOCK     | PASS+    | PASS+    |
| LOW     | PASS+         | PASS+     | PASS+    | PASS+    |
| INFO    | PASS+         | PASS+     | PASS+    | PASS+    |
+---------+---------------+-----------+----------+----------+

(``PASS+`` is shorthand for :data:`VisionVerdict.PASS_WITH_FINDINGS`.)
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.enums import AutonomyLevel
from synthorg.security.visionverify.models import (
    VisionFinding,
    VisionSeverity,
    VisionVerdict,
    severity_rank,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

SEVERITY_ALWAYS_BLOCK_FROM: Final[VisionSeverity] = VisionSeverity.HIGH
"""Severity at and above which the gate ALWAYS blocks, regardless of autonomy."""

SEVERITY_AUTONOMY_DEPENDENT: Final[VisionSeverity] = VisionSeverity.MEDIUM
"""Severity that blocks only under restrictive autonomy levels."""

AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM: Final[frozenset[AutonomyLevel]] = frozenset(
    {AutonomyLevel.LOCKED, AutonomyLevel.SUPERVISED},
)
"""Autonomy levels under which a MEDIUM finding routes to BLOCK."""


def should_block(severity: VisionSeverity, autonomy: AutonomyLevel) -> bool:
    """Return whether a single finding of ``severity`` blocks at ``autonomy``."""
    if severity_rank(severity) >= severity_rank(SEVERITY_ALWAYS_BLOCK_FROM):
        return True
    if severity is SEVERITY_AUTONOMY_DEPENDENT:
        return autonomy in AUTONOMY_LEVELS_THAT_BLOCK_MEDIUM
    return False


def compute_vision_verdict(
    findings: Sequence[VisionFinding],
    autonomy: AutonomyLevel,
) -> VisionVerdict:
    """Aggregate verdict for a sequence of findings under ``autonomy``.

    * Empty: :data:`VisionVerdict.PASS`.
    * Any blocking finding: :data:`VisionVerdict.BLOCK`.
    * Otherwise: :data:`VisionVerdict.PASS_WITH_FINDINGS`.

    Returns:
        The aggregate ``VisionVerdict`` for the findings under the given
        autonomy.
    """
    if not findings:
        return VisionVerdict.PASS
    for finding in findings:
        if should_block(finding.severity, autonomy):
            return VisionVerdict.BLOCK
    return VisionVerdict.PASS_WITH_FINDINGS
