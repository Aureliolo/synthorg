# module-kind: code
"""Run-outcome classification and stakes-aware risk derivation.

A small, import-cheap leaf so the approvals read layer, the review-approval
creation path, and the dashboard read model can all derive a run outcome and
map it onto an approval risk level without pulling in heavier engine or
persistence modules. Only depends on the enum leaves.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.task_enums import Stakes, TaskStatus

# Statuses for which a run has finished and a truthful outcome exists. This is a
# run-outcome concept (a finished run to judge), distinct from the task FSM's
# "terminal" states in ``task_enums``: FAILED is included here (its run is over)
# though the FSM allows reassigning it, and CANCELLED/REJECTED are excluded
# (they never ran, so there is no outcome to show). The single source of truth
# for the review queue, the live activity feed, and the overview breakdown.
TERMINAL_RUN_STATES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.IN_REVIEW, TaskStatus.COMPLETED, TaskStatus.FAILED}
)


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
    *,
    status: TaskStatus,
    produced_artifact_count: int,
    oracle_blocked: bool = False,
) -> RunOutcome:
    """Classify a task's run outcome from its status, artifacts, and oracle.

    The build/test completion oracle is the source of truth for a code
    task's success: a run whose oracle blocked (a REQUIRED code task whose
    tests failed or never ran) is ``FAILED`` even if it produced artifacts,
    so "succeeded" cannot mean "produced files that do not build". The
    caller computes ``oracle_blocked`` from the ``OracleEvaluation``'s
    ``blocks_completion`` property (kept out of this ``core`` leaf so it
    stays dependency-free).

    Args:
        status: Current task status.
        produced_artifact_count: Number of artifacts the run produced.
        oracle_blocked: Whether the build/test oracle blocked the run
            (a REQUIRED code task that is not verified). ``False`` when
            the oracle abstained, passed, or was not evaluated, which
            preserves the pre-oracle behaviour for every existing caller.

    Returns:
        ``FAILED`` for a failed task or an oracle-blocked run; ``EMPTY``
        for a review/completed run that produced nothing; ``SUCCEEDED``
        otherwise.
    """
    if status == TaskStatus.FAILED:
        return RunOutcome.FAILED
    if oracle_blocked:
        return RunOutcome.FAILED
    if (
        status in (TaskStatus.IN_REVIEW, TaskStatus.COMPLETED)
        and produced_artifact_count == 0
    ):
        return RunOutcome.EMPTY
    return RunOutcome.SUCCEEDED


# Base stakes -> risk mapping. Enum-to-enum (no magic numbers); the guard
# below forces a conscious entry when a new Stakes member is added.
_StakesRiskMap = MappingProxyType[Stakes, ApprovalRiskLevel]
_STAKES_BASE_RISK: Final[_StakesRiskMap] = MappingProxyType(
    {
        Stakes.LOW: ApprovalRiskLevel.LOW,
        Stakes.NORMAL: ApprovalRiskLevel.MEDIUM,
        Stakes.HIGH: ApprovalRiskLevel.HIGH,
        Stakes.CRITICAL: ApprovalRiskLevel.CRITICAL,
    }
)

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
_RISK_ORDER: Final[tuple[ApprovalRiskLevel, ...]] = (
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
    ``CRITICAL``) when the run failed or produced nothing, so a failure or
    empty run is never shown as ``LOW`` regardless of stakes.

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
