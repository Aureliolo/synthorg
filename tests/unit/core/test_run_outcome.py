"""Unit tests for run-outcome classification and risk derivation."""

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.run_outcome import (
    RunOutcome,
    derive_run_outcome,
    risk_from_task_outcome,
)
from synthorg.core.task_enums import Stakes, TaskStatus


@pytest.mark.unit
class TestDeriveRunOutcome:
    """A task's run outcome is derived from status + produced artifacts."""

    def test_failed_status_is_failed_even_with_artifacts(self) -> None:
        assert (
            derive_run_outcome(status=TaskStatus.FAILED, produced_artifact_count=3)
            == RunOutcome.FAILED
        )

    @pytest.mark.parametrize("status", [TaskStatus.IN_REVIEW, TaskStatus.COMPLETED])
    def test_zero_artifacts_is_empty(self, status: TaskStatus) -> None:
        assert (
            derive_run_outcome(status=status, produced_artifact_count=0)
            == RunOutcome.EMPTY
        )

    @pytest.mark.parametrize("status", [TaskStatus.IN_REVIEW, TaskStatus.COMPLETED])
    def test_with_artifacts_is_succeeded(self, status: TaskStatus) -> None:
        assert (
            derive_run_outcome(status=status, produced_artifact_count=2)
            == RunOutcome.SUCCEEDED
        )

    def test_in_progress_with_no_artifacts_is_not_empty(self) -> None:
        # Only review/completion states classify emptiness; an in-progress
        # run has not finished producing, so it is not "empty".
        assert (
            derive_run_outcome(status=TaskStatus.IN_PROGRESS, produced_artifact_count=0)
            == RunOutcome.SUCCEEDED
        )

    def test_oracle_blocked_is_failed_even_with_artifacts(self) -> None:
        # The completion oracle is the source of truth: a code task that
        # produced files but does not build/test is FAILED, not SUCCEEDED.
        assert (
            derive_run_outcome(
                status=TaskStatus.IN_REVIEW,
                produced_artifact_count=5,
                oracle_blocked=True,
            )
            == RunOutcome.FAILED
        )

    def test_oracle_not_blocked_preserves_prior_behaviour(self) -> None:
        assert (
            derive_run_outcome(
                status=TaskStatus.COMPLETED,
                produced_artifact_count=2,
                oracle_blocked=False,
            )
            == RunOutcome.SUCCEEDED
        )


@pytest.mark.unit
class TestRiskFromTaskOutcome:
    """Approval risk is derived from stakes, escalated on failure/emptiness."""

    @pytest.mark.parametrize(
        ("stakes", "expected"),
        [
            (Stakes.LOW, ApprovalRiskLevel.LOW),
            (Stakes.NORMAL, ApprovalRiskLevel.MEDIUM),
            (Stakes.HIGH, ApprovalRiskLevel.HIGH),
            (Stakes.CRITICAL, ApprovalRiskLevel.CRITICAL),
        ],
    )
    def test_succeeded_uses_base_map(
        self, stakes: Stakes, expected: ApprovalRiskLevel
    ) -> None:
        assert risk_from_task_outcome(stakes, RunOutcome.SUCCEEDED) == expected

    @pytest.mark.parametrize("outcome", [RunOutcome.FAILED, RunOutcome.EMPTY])
    @pytest.mark.parametrize(
        ("stakes", "expected"),
        [
            (Stakes.LOW, ApprovalRiskLevel.MEDIUM),
            (Stakes.NORMAL, ApprovalRiskLevel.HIGH),
            (Stakes.HIGH, ApprovalRiskLevel.CRITICAL),
            (Stakes.CRITICAL, ApprovalRiskLevel.CRITICAL),
        ],
    )
    def test_failure_escalates_one_level_capped_at_critical(
        self, outcome: RunOutcome, stakes: Stakes, expected: ApprovalRiskLevel
    ) -> None:
        assert risk_from_task_outcome(stakes, outcome) == expected

    def test_high_stakes_failure_never_reads_low(self) -> None:
        # The core bug: a high-stakes failed run must not read LOW.
        assert (
            risk_from_task_outcome(Stakes.HIGH, RunOutcome.FAILED)
            != ApprovalRiskLevel.LOW
        )
