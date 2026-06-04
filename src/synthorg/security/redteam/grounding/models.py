"""Grounding-claim domain models.

:class:`UngroundedClaim` is the unit produced by any
:class:`GroundingChecker` implementation. It carries enough context to
become a :class:`synthorg.security.redteam.models.RedTeamFinding` in
the gate, and enough metadata for the substrate-backed checker to layer
source-resolution data on top without breaking existing callers.
"""

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

HEURISTIC_CONFIDENCE_FLOOR: Final[float] = 0.4
"""Lower bound on heuristic-source claim confidence."""

HEURISTIC_CONFIDENCE_CEILING: Final[float] = 0.7
"""Upper bound on heuristic-source claim confidence.

Capping below 1.0 enforces the design contract that heuristic flags
never reach the gate's HIGH/CRITICAL routing tier on their own: only
the agent's own findings or substrate-backed claims may escalate above
the heuristic ceiling. The ceiling (0.7) sits above the substrate MEDIUM
floor by design, but the gate routes heuristic claims through
:data:`HEURISTIC_GROUNDING_MAX_SEVERITY` regardless of confidence, so the
overlap never escalates a heuristic claim.
See :mod:`synthorg.security.redteam.routing`.
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
        source: ``"heuristic"`` for heuristic-produced claims;
            ``"knowledge_substrate"`` for claims from the
            substrate-backed checker.
        expected_source_kind: When a checker knows what kind of source
            the claim should have traced to (e.g. ``"finance_report"``),
            it surfaces that here so the assignee knows what citation is
            missing. ``None`` when no specific source kind is known; both
            the heuristic and the current substrate checker emit ``None``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    excerpt: NotBlankStr
    reason: NotBlankStr
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["heuristic", "knowledge_substrate"]
    expected_source_kind: NotBlankStr | None = None

    @model_validator(mode="after")
    def _check_heuristic_confidence_bounds(self) -> Self:
        """Heuristic-source claims are bounded by floor / ceiling.

        Substrate-backed claims may use the full ``[0.0, 1.0]`` range
        because they are authoritative; the gate's routing layer caps
        heuristic-claim severity regardless, but enforcing
        the bound at construction prevents a buggy heuristic from
        accidentally smuggling a high-confidence flag past the cap.

        Returns:
            The validated claim.

        Raises:
            ValueError: If a heuristic-source claim's confidence falls
                outside the floor/ceiling band.
        """
        if self.source == "heuristic" and not (
            HEURISTIC_CONFIDENCE_FLOOR
            <= self.confidence
            <= HEURISTIC_CONFIDENCE_CEILING
        ):
            msg = (
                f"Heuristic-source claim confidence must be in "
                f"[{HEURISTIC_CONFIDENCE_FLOOR}, {HEURISTIC_CONFIDENCE_CEILING}]; "
                f"got {self.confidence}."
            )
            raise ValueError(msg)
        return self
