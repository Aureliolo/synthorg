# module-kind: code
"""A stall the replan trigger has re-confirmed against persistence.

Split out from :mod:`synthorg.engine.initiative.replan_trigger` for the same
reason :mod:`synthorg.engine.initiative.slice_state` was split out of the
trigger's other half: a self-contained type with no dependency on the
service's own collaborators, kept apart to leave room in that file's own
module-size budget.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan import Plan
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import (
    ITEM_DERIVED_STALLS,
    STAGE_OF_STALL_REASON,
    ItemProgress,
    StallReason,
    stall_reason,
)


class ConfirmedStall(BaseModel):
    """A stall the trigger has re-confirmed against persistence.

    Attributes:
        plan: The freshly read plan, still replannable and still stalled.
        reason: The stall shape derived from the live item statuses.
        items: Those item statuses, carried forward so the brief is built from
            the same read the verdict came from.
        detail: What the scheduling stage observed, when it knows something the
            item statuses do not.
        granted_by: Who authorised this replan, when a person did. Present
            means the successor is a human decision rather than the org acting
            unasked, which is what decides its generation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    plan: Plan = Field(description="The freshly read, still-stalled plan")
    reason: StallReason = Field(description="Live stall shape")
    items: tuple[ItemProgress, ...] = Field(description="Live item progress")
    detail: str | None = Field(
        default=None,
        description="What the scheduling stage observed",
    )
    granted_by: NotBlankStr | None = Field(
        default=None,
        description="Who authorised this replan, when a person did",
    )

    @model_validator(mode="after")
    def _validate_reason_matches_evidence(self) -> Self:
        """Reject a stall whose reason does not match what it carries.

        The type's whole claim is that it has been confirmed, and every
        consumer builds the successor's brief on that basis. Checking it here
        keeps the guarantee attached to the type rather than resting on the one
        private method that happens to construct it correctly today.

        Returns:
            The validated model.

        Raises:
            ValueError: When the reason contradicts the items or the plan's
                own status.
        """
        if self.reason in ITEM_DERIVED_STALLS:
            if stall_reason(self.items) is not self.reason:
                msg = "reason does not match the live item stall shape"
                raise ValueError(msg)
        elif self.plan.status is not STAGE_OF_STALL_REASON[self.reason]:
            msg = "reason does not match the plan's tail stage"
            raise ValueError(msg)
        return self


__all__ = ["ConfirmedStall"]
