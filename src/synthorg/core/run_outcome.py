# module-kind: code
"""Run-outcome classification and stakes-aware risk derivation.

A small, import-cheap leaf so the approvals read layer, the review-approval
creation path, and the dashboard read model can all derive a run outcome and
map it onto an approval risk level without pulling in heavier engine or
persistence modules. Only depends on the enum leaves.
"""

from enum import StrEnum

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.task_enums import Stakes, TaskStatus


class RunOutcome(StrEnum):
    """Truthful outcome of a task run, for failure-aware review surfaces.

    Attributes:
        SUCCEEDED: The run produced artifacts (a genuine completion).
        EMPTY: The run reached review/completion but produced nothing.
        FAILED: The run failed (fail-loud empty-run or a hard error).
    """

    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    FAILED = "failed"


def derive_run_outcome(
    *, status: TaskStatus, produced_artifact_count: int
) -> RunOutcome:
    """Classify a task's run outcome from its status and produced artifacts.

    Args:
        status: Current task status.
        produced_artifact_count: Number of artifacts the run produced.

    Returns:
        ``FAILED`` for a failed task; ``EMPTY`` for a review/completed run
        that produced nothing; ``SUCCEEDED`` otherwise.
    """
    if status == TaskStatus.FAILED:
        return RunOutcome.FAILED
    if (
        status in (TaskStatus.IN_REVIEW, TaskStatus.COMPLETED)
        and produced_artifact_count == 0
    ):
        return RunOutcome.EMPTY
    return RunOutcome.SUCCEEDED


# Base stakes -> risk mapping. Enum-to-enum (no magic numbers); the guard
# below forces a conscious entry when a new Stakes member is added.
_STAKES_BASE_RISK: dict[Stakes, ApprovalRiskLevel] = {
    Stakes.LOW: ApprovalRiskLevel.LOW,
    Stakes.NORMAL: ApprovalRiskLevel.MEDIUM,
    Stakes.HIGH: ApprovalRiskLevel.HIGH,
    Stakes.CRITICAL: ApprovalRiskLevel.CRITICAL,
}

_missing_stakes = set(Stakes) - set(_STAKES_BASE_RISK)
if _missing_stakes:
    _stakes_msg = (
        f"_STAKES_BASE_RISK missing entries for: "
        f"{sorted(s.value for s in _missing_stakes)}"
    )
    raise RuntimeError(_stakes_msg)
del _missing_stakes

# Risk escalation order, lowest to highest. Explicit literal so a step is a
# conscious placement, not a dynamic ``tuple(ApprovalRiskLevel)``.
_RISK_ORDER: tuple[ApprovalRiskLevel, ...] = (
    ApprovalRiskLevel.LOW,
    ApprovalRiskLevel.MEDIUM,
    ApprovalRiskLevel.HIGH,
    ApprovalRiskLevel.CRITICAL,
)

if set(_RISK_ORDER) != set(ApprovalRiskLevel):
    _risk_msg = f"_RISK_ORDER out of sync: {set(_RISK_ORDER) ^ set(ApprovalRiskLevel)}"
    raise RuntimeError(_risk_msg)


def _escalate_one(risk: ApprovalRiskLevel) -> ApprovalRiskLevel:
    """Return the next-higher risk level, capped at the top of the order."""
    idx = _RISK_ORDER.index(risk)
    next_idx = min(idx + 1, len(_RISK_ORDER) - 1)
    return _RISK_ORDER[next_idx]


def risk_from_task_outcome(stakes: Stakes, outcome: RunOutcome) -> ApprovalRiskLevel:
    """Derive an approval risk level from task stakes and run outcome.

    Maps stakes to a base risk, then escalates one level (capped at
    ``CRITICAL``) when the run failed or produced nothing, so a high-stakes
    failure never reads ``LOW``.

    Args:
        stakes: The task's stakes.
        outcome: The run outcome.

    Returns:
        The derived approval risk level.
    """
    base = _STAKES_BASE_RISK[stakes]
    if outcome in (RunOutcome.FAILED, RunOutcome.EMPTY):
        return _escalate_one(base)
    return base
