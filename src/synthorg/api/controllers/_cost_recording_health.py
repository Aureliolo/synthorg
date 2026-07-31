# module-kind: code
"""Cost-recording health for the readiness surface.

Recording a cost is best-effort by design: losing the record must never fail
the user's LLM call, so a failure is caught, logged and dropped. That is the
right trade for a blip and the wrong one for a standing fault, because the
spend still happens either way. Every dropped record is money the budget does
not know about, and some causes never clear on their own: a tracker whose
configured currency disagrees with the record's rejects every write for the
lifetime of the process.

Reported here so that condition is visible where an operator already looks,
rather than only in a log line they would have to know to search for.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.providers.cost_recording import (
    COST_FAILURE_ESCALATION_STREAK,
    consecutive_cost_failures,
)


class CostRecordingState(StrEnum):
    """Whether spend is currently being recorded.

    Attributes:
        OK: Records are landing, or have failed too few times in a row to
            distinguish a blip from a fault.
        DEGRADED: Enough records have failed back to back that the budget is
            under-reporting for as long as it lasts.
    """

    OK = "ok"
    DEGRADED = "degraded"


class CostRecordingHealth(BaseModel):
    """Cost-recording state for this process.

    Deliberately outside the readiness roll-up: an organisation whose spend is
    not being recorded still serves every request correctly, and failing
    readiness would have a supervisor restart it into the same condition. It
    is an operator's problem to fix, not a reason to cycle the deployment.

    Attributes:
        state: Whether spend is currently being recorded.
        dropped_records: How many records have failed back to back.
        detail: What the failure means, when there is one.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    state: CostRecordingState = Field(description="Whether spend is being recorded")
    dropped_records: int = Field(
        default=0, ge=0, description="Consecutive failed cost writes"
    )
    detail: str | None = Field(
        default=None,
        description="Operator-facing explanation, when action is needed",
    )


def resolve_cost_recording_health() -> CostRecordingHealth:
    """Report whether cost records are landing.

    Reads the recorder's own failure streak rather than probing: a probe would
    have to write a record to find out, which is the thing that is failing.

    Nothing is logged here. The recorder already escalates to ERROR when the
    streak reaches the threshold, and this resolver answers every poll of
    ``/health``, so logging would repeat a standing condition on a timer.

    Returns:
        ``CostRecordingHealth`` describing the current streak.
    """
    streak = consecutive_cost_failures()
    if streak < COST_FAILURE_ESCALATION_STREAK:
        return CostRecordingHealth(
            state=CostRecordingState.OK,
            dropped_records=streak,
        )
    return CostRecordingHealth(
        state=CostRecordingState.DEGRADED,
        dropped_records=streak,
        detail=(
            f"{streak} cost records in a row failed to persist, so recorded "
            "spend is lower than actual spend until this clears. Check the "
            "budget tracker's configured currency and the persistence "
            "backend."
        ),
    )


__all__ = [
    "CostRecordingHealth",
    "CostRecordingState",
    "resolve_cost_recording_health",
]
