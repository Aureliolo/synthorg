"""Convert heuristic grounding claims into red-team findings.

Pure, side-effect-free helpers split out of :mod:`gate` so the gate
module stays focused on orchestration. A heuristic
:class:`UngroundedClaim` becomes a ``source="heuristic"``
:class:`RedTeamFinding` on the GROUNDING surface, capped at
:data:`HEURISTIC_GROUNDING_MAX_SEVERITY` so the stub can never block on
its own -- only the LLM agent or a substrate-backed checker may file a
blocking grounding finding.
"""

from typing import Final

from synthorg.security.redteam.grounding.models import UngroundedClaim
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
)
from synthorg.security.redteam.routing import HEURISTIC_GROUNDING_MAX_SEVERITY

_MAX_EVIDENCE_EXCERPT_CHARS: Final[int] = 240
"""Cap on the length of a heuristic-derived evidence excerpt.

Long excerpts pollute the rework brief; the cap keeps a finding
self-contained while leaving room for one full sentence.
"""

_ELLIPSIS: Final[str] = "..."
_ELLIPSIS_OVERHEAD: Final[int] = len(_ELLIPSIS)
"""Characters the truncation ellipsis consumes, reserved from the cap."""


def evidence_excerpt(
    claim: UngroundedClaim,
    *,
    max_chars: int = _MAX_EVIDENCE_EXCERPT_CHARS,
) -> str:
    """Truncate a claim's excerpt to a bounded length for finding evidence.

    Returns:
        The excerpt, truncated with an ellipsis when it exceeds
        ``max_chars``.
    """
    if len(claim.excerpt) <= max_chars:
        return claim.excerpt
    return f"{claim.excerpt[: max_chars - _ELLIPSIS_OVERHEAD]}{_ELLIPSIS}"


def claim_to_finding(claim: UngroundedClaim) -> RedTeamFinding:
    """Convert a heuristic :class:`UngroundedClaim` into a :class:`RedTeamFinding`.

    Always at or below :data:`HEURISTIC_GROUNDING_MAX_SEVERITY` (LOW)
    so the stub cannot block on its own; only the LLM agent or a
    substrate-backed checker may file blocking grounding findings.

    Returns:
        A ``RedTeamFinding`` for the grounding surface, capped at the
        heuristic max severity.
    """
    return RedTeamFinding(
        attack_surface=RedTeamAttackSurface.GROUNDING,
        severity=HEURISTIC_GROUNDING_MAX_SEVERITY,
        description=f"Ungrounded claim: {claim.reason}",
        evidence=(evidence_excerpt(claim),),
        suggested_fix=(
            "Cite the originating source for this claim or remove the "
            "assertion. Hedging language is also acceptable for soft claims."
        ),
        source=claim.source,
        citations=(),
    )
