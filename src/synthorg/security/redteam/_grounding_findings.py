# module-kind: code
"""Convert grounding claims into red-team findings.

Pure, side-effect-free helpers split out of :mod:`gate` so the gate
module stays focused on orchestration. An :class:`UngroundedClaim`
becomes a :class:`RedTeamFinding` on the GROUNDING surface; the severity
is source-aware. ``source="heuristic"`` claims are capped at
:data:`HEURISTIC_GROUNDING_MAX_SEVERITY` (LOW) so the deterministic
heuristic can never block on its own. ``source="knowledge_substrate"`` claims map
their ungrounded-confidence through
:func:`substrate_severity_for_confidence` (up to the HIGH cap), which is
how the substrate-backed checker escalates to a blocking grounding
finding the LLM agent did not raise.
"""

from typing import Final

from synthorg.security.redteam.grounding.models import UngroundedClaim
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamSeverity,
)
from synthorg.security.redteam.routing import (
    HEURISTIC_GROUNDING_MAX_SEVERITY,
    substrate_severity_for_confidence,
)

_MAX_EVIDENCE_EXCERPT_CHARS: Final[int] = 240
"""Cap on the length of a claim's evidence excerpt in a finding.

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
    if max_chars <= 0:
        return ""
    # Verbatim fast-path first: an excerpt that already fits is returned as-is,
    # even under a tiny cap (a 1-char excerpt with max_chars=1 must not become
    # an ellipsis).
    if len(claim.excerpt) <= max_chars:
        return claim.excerpt
    # A cap at or below the ellipsis width cannot fit "..." plus any prefix;
    # return as much of the ellipsis as the cap allows so the result never
    # exceeds max_chars.
    if max_chars <= _ELLIPSIS_OVERHEAD:
        return _ELLIPSIS[:max_chars]
    keep = max_chars - _ELLIPSIS_OVERHEAD
    return f"{claim.excerpt[:keep]}{_ELLIPSIS}"


def _claim_severity(claim: UngroundedClaim) -> RedTeamSeverity | None:
    """Resolve the finding severity for ``claim`` from its source.

    ``heuristic`` claims are pinned to :data:`HEURISTIC_GROUNDING_MAX_SEVERITY`
    (LOW). ``knowledge_substrate`` claims map their ungrounded-confidence
    through :func:`substrate_severity_for_confidence`, which returns ``None``
    below the substrate drop floor: a sub-floor substrate claim is dropped,
    not coerced into a LOW finding, so the checker's "drop below the floor"
    contract holds end-to-end instead of reintroducing the low-confidence
    noise the substrate checker exists to suppress.

    Returns:
        The severity tier for the claim, or ``None`` when a substrate claim
        falls below the drop floor and must not become a finding.
    """
    if claim.source == "knowledge_substrate":
        return substrate_severity_for_confidence(claim.confidence)
    return HEURISTIC_GROUNDING_MAX_SEVERITY


def _suggested_fix(claim: UngroundedClaim) -> str:
    """Build the rework hint, naming the expected source kind when known.

    Returns:
        The suggested-fix prose for the finding.
    """
    base = (
        "Cite the originating source for this claim or remove the "
        "assertion. Hedging language is also acceptable for soft claims."
    )
    if claim.expected_source_kind is not None:
        return f"{base} Expected source kind: {claim.expected_source_kind}."
    return base


def claim_to_finding(claim: UngroundedClaim) -> RedTeamFinding | None:
    """Convert an :class:`UngroundedClaim` into a :class:`RedTeamFinding`.

    Severity is source-aware (see :func:`_claim_severity`): heuristic
    claims stay at LOW; substrate claims escalate by confidence up to the
    HIGH cap, which is how the substrate-backed checker produces a blocking
    grounding finding. The claim excerpt provides the evidence entry that
    HIGH-severity findings require.

    Returns:
        A ``RedTeamFinding`` on the grounding surface, or ``None`` when the
        claim is a below-floor substrate claim that must be dropped rather
        than surfaced.
    """
    severity = _claim_severity(claim)
    if severity is None:
        return None
    return RedTeamFinding(
        attack_surface=RedTeamAttackSurface.GROUNDING,
        severity=severity,
        description=f"Ungrounded claim: {claim.reason}",
        evidence=(evidence_excerpt(claim),),
        suggested_fix=_suggested_fix(claim),
        source=claim.source,
        citations=(),
    )
