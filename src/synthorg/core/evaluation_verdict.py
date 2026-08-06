# module-kind: code
"""One criterion's verdict, and whether it was met.

Lives in ``core`` because the verdict crosses layers: the evaluate stage
produces it, persistence stores it, and the API renders it. A shared shape
in a leaf module keeps persistence from importing the engine to describe
the thing it is asked to write down.
"""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr

#: Ceiling on any one free-text field a verdict carries. Generous enough for
#: a real piece of evidence, small enough that a runaway generation is
#: refused rather than parsed.
MAX_VERDICT_TEXT_LENGTH: Final[int] = 4000


class CriterionOutcome(StrEnum):
    """Whether one success criterion is met by the delivered whole.

    ``MET``: the criterion holds, with evidence. ``PARTIAL``: partly holds, so
    the objective is not delivered. ``UNMET``: does not hold.
    """

    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"


class CriterionVerdict(BaseModel):
    """One criterion's verdict with the evidence behind it.

    Attributes:
        criterion: The objective criterion being judged, quoted back so the
            report is readable without cross-referencing the plan.
        outcome: Whether it is met, partly met, or unmet.
        evidence: What was observed that supports the outcome. Required
            regardless of outcome: an unevidenced pass is a guess, and an
            unevidenced failure gives the replan nothing to work from.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    criterion: NotBlankStr = Field(
        max_length=MAX_VERDICT_TEXT_LENGTH,
        description="The objective criterion judged",
    )
    outcome: CriterionOutcome = Field(description="Met, partial, or unmet")
    evidence: NotBlankStr = Field(
        max_length=MAX_VERDICT_TEXT_LENGTH,
        description="What was observed",
    )


__all__ = ["MAX_VERDICT_TEXT_LENGTH", "CriterionOutcome", "CriterionVerdict"]
