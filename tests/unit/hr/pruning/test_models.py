"""Tests for pruning domain models."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from synthorg.approval.enums import ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.pruning.models import (
    PruningEvaluation,
    PruningRecord,
    PruningRequest,
    PruningServiceConfig,
)

from .conftest import NOW, make_performance_snapshot

# ── PruningEvaluation ────────────────────────────────────────────


@pytest.mark.unit
class TestPruningEvaluation:
    """PruningEvaluation construction, frozen enforcement, validation."""

    def test_valid_eligible(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("quality below 3.5 in 7d and 30d windows"),),
            scores={"quality": 2.1, "collaboration": 3.0},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        assert evaluation.eligible is True
        assert len(evaluation.reasons) == 1
        assert evaluation.policy_name == "threshold"
        assert evaluation.scores["quality"] == 2.1

    def test_valid_ineligible(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=False,
            reasons=(),
            scores={"quality": 7.0},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        assert evaluation.eligible is False
        assert evaluation.reasons == ()

    def test_frozen_enforcement(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        with pytest.raises(ValidationError):
            evaluation.eligible = False  # type: ignore[misc]

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PruningEvaluation(
                agent_id=NotBlankStr(""),
                eligible=False,
                reasons=(),
                scores={},
                policy_name=NotBlankStr("threshold"),
                snapshot=make_performance_snapshot(),
                evaluated_at=NOW,
            )

    def test_blank_policy_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PruningEvaluation(
                agent_id=NotBlankStr("agent-001"),
                eligible=False,
                reasons=(),
                scores={},
                policy_name=NotBlankStr(""),
                snapshot=make_performance_snapshot(),
                evaluated_at=NOW,
            )

    def test_eligible_with_empty_reasons_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"eligible.*must have.*reason"):
            PruningEvaluation(
                agent_id=NotBlankStr("agent-001"),
                eligible=True,
                reasons=(),
                scores={},
                policy_name=NotBlankStr("threshold"),
                snapshot=make_performance_snapshot(),
                evaluated_at=NOW,
            )

    def test_ineligible_with_empty_reasons_allowed(self) -> None:
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=False,
            reasons=(),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=make_performance_snapshot(),
            evaluated_at=NOW,
        )
        assert evaluation.reasons == ()


# ── PruningRequest ───────────────────────────────────────────────


@pytest.mark.unit
class TestPruningRequest:
    """PruningRequest construction, id generation, temporal validation."""

    def test_valid_construction(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("quality below threshold"),),
            scores={"quality": 2.0},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        request = PruningRequest(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            evaluation=evaluation,
            approval_id=NotBlankStr("approval-001"),
            status=ApprovalStatus.PENDING,
            created_at=NOW,
        )
        assert request.agent_id == "agent-001"
        assert request.agent_name == "test-agent"
        assert request.status == ApprovalStatus.PENDING
        assert request.decided_at is None

    def test_id_auto_generated(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        r1 = PruningRequest(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            evaluation=evaluation,
            approval_id=NotBlankStr("a-001"),
            created_at=NOW,
        )
        r2 = PruningRequest(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            evaluation=evaluation,
            approval_id=NotBlankStr("a-002"),
            created_at=NOW,
        )
        assert r1.id != r2.id

    def test_frozen_enforcement(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        request = PruningRequest(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            evaluation=evaluation,
            approval_id=NotBlankStr("a-001"),
            created_at=NOW,
        )
        with pytest.raises(ValidationError):
            request.status = ApprovalStatus.APPROVED  # type: ignore[misc]

    def test_decided_at_before_created_at_rejected(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        with pytest.raises(ValidationError, match=r"decided_at.*created_at"):
            PruningRequest(
                agent_id=NotBlankStr("agent-001"),
                agent_name=NotBlankStr("test-agent"),
                evaluation=evaluation,
                approval_id=NotBlankStr("a-001"),
                status=ApprovalStatus.APPROVED,
                created_at=NOW,
                decided_at=NOW - timedelta(hours=1),
                decided_by=NotBlankStr("admin"),
            )

    def test_valid_decided_fields(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        request = PruningRequest(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            evaluation=evaluation,
            approval_id=NotBlankStr("a-001"),
            status=ApprovalStatus.APPROVED,
            created_at=NOW,
            decided_at=NOW + timedelta(hours=1),
            decided_by=NotBlankStr("admin"),
        )
        assert request.decided_at == NOW + timedelta(hours=1)
        assert request.decided_by == "admin"

    def test_approved_without_decided_fields_rejected(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        with pytest.raises(ValidationError, match="decided_at and decided_by"):
            PruningRequest(
                agent_id=NotBlankStr("agent-001"),
                agent_name=NotBlankStr("test-agent"),
                evaluation=evaluation,
                approval_id=NotBlankStr("a-001"),
                status=ApprovalStatus.APPROVED,
                created_at=NOW,
            )

    def test_expired_without_decided_fields_allowed(self) -> None:
        """EXPIRED is treated like PENDING -- no decided fields required."""
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        request = PruningRequest(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            evaluation=evaluation,
            approval_id=NotBlankStr("a-001"),
            status=ApprovalStatus.EXPIRED,
            created_at=NOW,
        )
        assert request.status == ApprovalStatus.EXPIRED
        assert request.decided_at is None
        assert request.decided_by is None

    def test_expired_with_decided_fields_rejected(self) -> None:
        """EXPIRED should not have decided_at/decided_by."""
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        with pytest.raises(ValidationError, match="decided_at and decided_by"):
            PruningRequest(
                agent_id=NotBlankStr("agent-001"),
                agent_name=NotBlankStr("test-agent"),
                evaluation=evaluation,
                approval_id=NotBlankStr("a-001"),
                status=ApprovalStatus.EXPIRED,
                created_at=NOW,
                decided_at=NOW + timedelta(hours=1),
                decided_by=NotBlankStr("admin"),
            )

    def test_pending_with_decided_fields_rejected(self) -> None:
        snapshot = make_performance_snapshot()
        evaluation = PruningEvaluation(
            agent_id=NotBlankStr("agent-001"),
            eligible=True,
            reasons=(NotBlankStr("test reason"),),
            scores={},
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )
        with pytest.raises(ValidationError, match="decided_at and decided_by"):
            PruningRequest(
                agent_id=NotBlankStr("agent-001"),
                agent_name=NotBlankStr("test-agent"),
                evaluation=evaluation,
                approval_id=NotBlankStr("a-001"),
                status=ApprovalStatus.PENDING,
                created_at=NOW,
                decided_at=NOW + timedelta(hours=1),
                decided_by=NotBlankStr("admin"),
            )


# ── PruningRecord ───────────────────────────────────────────────


@pytest.mark.unit
class TestPruningRecord:
    """PruningRecord construction and temporal validation."""

    def test_valid_construction(self) -> None:
        record = PruningRecord(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            pruning_request_id=NotBlankStr("req-001"),
            firing_request_id=NotBlankStr("fire-001"),
            reason=NotBlankStr("quality below threshold"),
            approval_id=NotBlankStr("approval-001"),
            initiated_by=NotBlankStr("system"),
            created_at=NOW,
            completed_at=NOW + timedelta(minutes=5),
        )
        assert record.agent_id == "agent-001"
        assert record.firing_request_id == "fire-001"

    def test_frozen_enforcement(self) -> None:
        record = PruningRecord(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            pruning_request_id=NotBlankStr("req-001"),
            firing_request_id=NotBlankStr("fire-001"),
            reason=NotBlankStr("test"),
            approval_id=NotBlankStr("a-001"),
            initiated_by=NotBlankStr("system"),
            created_at=NOW,
            completed_at=NOW + timedelta(minutes=1),
        )
        with pytest.raises(ValidationError):
            record.agent_id = "other"  # type: ignore[misc]

    def test_completed_before_created_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"completed_at.*created_at"):
            PruningRecord(
                agent_id=NotBlankStr("agent-001"),
                agent_name=NotBlankStr("test-agent"),
                pruning_request_id=NotBlankStr("req-001"),
                firing_request_id=NotBlankStr("fire-001"),
                reason=NotBlankStr("test"),
                approval_id=NotBlankStr("a-001"),
                initiated_by=NotBlankStr("system"),
                created_at=NOW,
                completed_at=NOW - timedelta(minutes=1),
            )

    def test_same_created_and_completed_allowed(self) -> None:
        record = PruningRecord(
            agent_id=NotBlankStr("agent-001"),
            agent_name=NotBlankStr("test-agent"),
            pruning_request_id=NotBlankStr("req-001"),
            firing_request_id=NotBlankStr("fire-001"),
            reason=NotBlankStr("test"),
            approval_id=NotBlankStr("a-001"),
            initiated_by=NotBlankStr("system"),
            created_at=NOW,
            completed_at=NOW,
        )
        assert record.completed_at == record.created_at


# ── PruningJobRun ────────────────────────────────────────────────


@pytest.mark.unit
class TestPruningJobRun:
    """PruningJobRun count relationship validation."""

    def test_eligible_exceeds_evaluated_rejected(self) -> None:
        from synthorg.hr.pruning.models import PruningJobRun

        with pytest.raises(ValidationError, match=r"agents_eligible.*cannot exceed"):
            PruningJobRun(
                job_id=NotBlankStr("job-001"),
                run_at=NOW,
                agents_evaluated=5,
                agents_eligible=10,
                approval_requests_created=0,
                elapsed_seconds=1.0,
            )

    def test_approvals_exceeds_eligible_rejected(self) -> None:
        from synthorg.hr.pruning.models import PruningJobRun

        with pytest.raises(
            ValidationError,
            match=r"approval_requests_created.*cannot exceed",
        ):
            PruningJobRun(
                job_id=NotBlankStr("job-001"),
                run_at=NOW,
                agents_evaluated=10,
                agents_eligible=3,
                approval_requests_created=5,
                elapsed_seconds=1.0,
            )

    def test_valid_counts(self) -> None:
        from synthorg.hr.pruning.models import PruningJobRun

        job = PruningJobRun(
            job_id=NotBlankStr("job-001"),
            run_at=NOW,
            agents_evaluated=10,
            agents_eligible=3,
            approval_requests_created=2,
            elapsed_seconds=1.5,
        )
        assert job.agents_evaluated == 10
        assert job.agents_eligible == 3
        assert job.approval_requests_created == 2


# ── PruningServiceConfig ────────────────────────────────────────


@pytest.mark.unit
class TestPruningServiceConfig:
    """PruningServiceConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = PruningServiceConfig()
        assert config.evaluation_interval_seconds == 3600.0
        assert config.max_approvals_per_cycle == 5
        assert config.approval_expiry_days == 7

    @pytest.mark.parametrize(
        "interval",
        [60.0, 3600.0, 86400.0],
        ids=["min_boundary", "default", "one_day"],
    )
    def test_valid_intervals(self, interval: float) -> None:
        config = PruningServiceConfig(evaluation_interval_seconds=interval)
        assert config.evaluation_interval_seconds == interval

    def test_interval_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PruningServiceConfig(evaluation_interval_seconds=59.9)

    def test_max_approvals_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PruningServiceConfig(max_approvals_per_cycle=0)

    def test_approval_expiry_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PruningServiceConfig(approval_expiry_days=0)

    def test_frozen_enforcement(self) -> None:
        config = PruningServiceConfig()
        with pytest.raises(ValidationError):
            config.evaluation_interval_seconds = 999.0  # type: ignore[misc]
