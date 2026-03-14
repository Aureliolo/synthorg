"""Tests for the ApprovalItem domain model."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.mark.unit
class TestApprovalItem:
    def test_valid_pending_item(self) -> None:
        item = ApprovalItem(
            id="approval-abc",
            action_type="code_merge",
            title="Merge PR #42",
            description="Merging feature branch",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.MEDIUM,
            created_at=_now(),
        )
        assert item.status == ApprovalStatus.PENDING
        assert item.decided_at is None
        assert item.decided_by is None

    def test_pending_must_not_have_decided_fields(self) -> None:
        with pytest.raises(ValueError, match="decided_at and decided_by must be None"):
            ApprovalItem(
                id="approval-abc",
                action_type="code_merge",
                title="Merge PR",
                description="desc",
                requested_by="agent-dev",
                risk_level=ApprovalRiskLevel.LOW,
                status=ApprovalStatus.PENDING,
                created_at=_now(),
                decided_at=_now(),
                decided_by="ceo",
            )

    def test_approved_requires_decided_fields(self) -> None:
        now = _now()
        item = ApprovalItem(
            id="approval-abc",
            action_type="deployment",
            title="Deploy v2",
            description="Production deploy",
            requested_by="agent-ops",
            risk_level=ApprovalRiskLevel.HIGH,
            status=ApprovalStatus.APPROVED,
            created_at=now,
            decided_at=now + timedelta(minutes=5),
            decided_by="ceo",
        )
        assert item.status == ApprovalStatus.APPROVED
        assert item.decided_by == "ceo"

    def test_approved_missing_decided_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="decided_at and decided_by are required"):
            ApprovalItem(
                id="approval-abc",
                action_type="deployment",
                title="Deploy v2",
                description="desc",
                requested_by="agent-ops",
                risk_level=ApprovalRiskLevel.HIGH,
                status=ApprovalStatus.APPROVED,
                created_at=_now(),
            )

    def test_rejected_requires_decision_reason(self) -> None:
        now = _now()
        with pytest.raises(ValueError, match="at least 1 character"):
            ApprovalItem(
                id="approval-abc",
                action_type="budget_spend",
                title="Big purchase",
                description="desc",
                requested_by="agent-cfo",
                risk_level=ApprovalRiskLevel.CRITICAL,
                status=ApprovalStatus.REJECTED,
                created_at=now,
                decided_at=now + timedelta(minutes=1),
                decided_by="ceo",
                decision_reason="",
            )

    def test_rejected_with_reason_is_valid(self) -> None:
        now = _now()
        item = ApprovalItem(
            id="approval-abc",
            action_type="budget_spend",
            title="Big purchase",
            description="desc",
            requested_by="agent-cfo",
            risk_level=ApprovalRiskLevel.CRITICAL,
            status=ApprovalStatus.REJECTED,
            created_at=now,
            decided_at=now + timedelta(minutes=1),
            decided_by="ceo",
            decision_reason="Too expensive",
        )
        assert item.decision_reason == "Too expensive"

    def test_expired_must_not_have_decided_fields(self) -> None:
        now = _now()
        item = ApprovalItem(
            id="approval-abc",
            action_type="code_merge",
            title="Merge",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            status=ApprovalStatus.EXPIRED,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        assert item.status == ApprovalStatus.EXPIRED

    def test_expires_at_must_be_after_created_at(self) -> None:
        now = _now()
        with pytest.raises(ValueError, match="expires_at must be after created_at"):
            ApprovalItem(
                id="approval-abc",
                action_type="code_merge",
                title="Merge",
                description="desc",
                requested_by="agent-dev",
                risk_level=ApprovalRiskLevel.LOW,
                created_at=now,
                expires_at=now - timedelta(hours=1),
            )

    def test_metadata_defaults_to_empty(self) -> None:
        item = ApprovalItem(
            id="approval-abc",
            action_type="code_merge",
            title="Merge",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=_now(),
        )
        assert item.metadata == {}

    def test_with_task_id_and_metadata(self) -> None:
        item = ApprovalItem(
            id="approval-abc",
            action_type="code_merge",
            title="Merge",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            created_at=_now(),
            task_id="task-001",
            metadata={"pr": "42"},
        )
        assert item.task_id == "task-001"
        assert item.metadata == {"pr": "42"}
