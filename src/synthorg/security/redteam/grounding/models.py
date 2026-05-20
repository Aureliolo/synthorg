"""Grounding-claim domain models.

:class:`UngroundedClaim` is the unit produced by any
:class:`GroundingChecker` implementation. It carries enough context to
become a :class:`synthorg.security.redteam.models.RedTeamFinding` in
the gate, and enough metadata that the EPIC E #1988 substrate-backed
checker can layer source-resolution data on top without breaking
existing callers.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001

HEURISTIC_CONFIDENCE_FLOOR: Final[float] = 0.4
"""Lower bound on heuristic-source claim confidence."""

HEURISTIC_CONFIDENCE_CEILING: Final[float] = 0.7
"""Upper bound on heuristic-source claim confidence.

Capping below 1.0 enforces the design contract that heuristic flags
never reach the gate's HIGH/CRITICAL routing tier on their own: only
the agent's own findings (or post-#1988 substrate-backed claims) may
escalate above the heuristic ceiling. See
:mod:`synthorg.security.redteam.routing`.
"""


class UngroundedClaim(BaseModel):
    """A claim in a deliverable that lacks a traceable source.

    Attributes:
        excerpt: The suspect sentence or fragment, copied verbatim
            from the deliverable so the assignee can locate it on
            rework.
        reason: Short human-readable rationale for the flag
            (e.g. "asserts a percentage with no citation marker").
        confidence: Confidence in the flag, in ``[0.0, 1.0]``.
            Heuristic checkers are bounded by
            :data:`HEURISTIC_CONFIDENCE_FLOOR` /
            :data:`HEURISTIC_CONFIDENCE_CEILING`; substrate-backed
            checkers may use the full range.
        source: ``"heuristic"`` for stub-produced claims;
            ``"knowledge_substrate"`` reserved for the EPIC E #1988
            substrate-backed implementation.
        expected_source_kind: When the substrate-backed checker knows
            what kind of source the claim should have traced to
            (e.g. ``"finance_report"``), it surfaces that here so the
            assignee knows what citation is missing. ``None`` for
            heuristic claims.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    excerpt: NotBlankStr
    reason: NotBlankStr
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["heuristic", "knowledge_substrate"]
    expected_source_kind: NotBlankStr | None = None
