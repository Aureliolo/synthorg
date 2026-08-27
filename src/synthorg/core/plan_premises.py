# module-kind: declarative
"""What a plan revision rests on, carried beside the items that rest on it."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class PlanPremises(BaseModel):
    """What a plan revision rests on, carried beside the items that rest on it.

    Assumptions and open questions belong to the pass that derived them, and a
    live run showed what happens when they do not travel with it: a re-plan
    replaced every item with "build the engine from scratch" while the plan
    went on asserting the engine already existed, because the rework path
    carried the superseded plan's premises forward. The plan contradicted
    itself, and the false assumption the operator had just refuted was the one
    left standing.

    Attributes:
        assumptions: What the revision takes as given.
        open_questions: What it could not settle and needs a human for.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    assumptions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="What this revision takes as given",
    )
    open_questions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="What this revision could not settle",
    )


__all__ = ["PlanPremises"]
