"""Tests for HR domain models."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.enums import SeniorityLevel
from synthorg.hr.enums import (
    FiringReason,
    HiringRequestStatus,
    LifecycleEventType,
    OnboardingStep,
)
from synthorg.hr.models import (
    AgentLifecycleEvent,
    OffboardingRecord,
    OnboardingChecklist,
    OnboardingStepRecord,
)
from tests.unit.hr.conftest import (
    make_candidate_card,
    make_firing_request,
    make_hiring_request,
)

# ── CandidateCard ──────────────────────────────────────────────


@pytest.mark.unit
class TestCandidateCard:
    """CandidateCard construction and constraints."""

    def test_construction_minimal(self) -> None:
        card = make_candidate_card()
        assert card.name == "candidate-agent"
        assert card.role == "developer"
        assert card.department == "engineering"
        assert card.level == SeniorityLevel.MID
        assert card.estimated_monthly_cost == 50.0

    def test_construction_with_skills(self) -> None:
        card = make_candidate_card(skills=("python", "rust"))
        assert len(card.skills) == 2
        assert card.skills[0] == "python"

    def test_frozen(self) -> None:
        card = make_candidate_card()
        with pytest.raises(ValidationError):
            card.name = "new-name"  # type: ignore[misc]

    def test_defaults(self) -> None:
        card = make_candidate_card()
        assert card.skills == ()
        assert card.template_source is None

    def test_auto_generated_id(self) -> None:
        card_a = make_candidate_card()
        card_b = make_candidate_card()
        assert card_a.id != card_b.id

    def test_explicit_id(self) -> None:
        card = make_candidate_card(candidate_id="custom-id")
        assert card.id == "custom-id"

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal"):
            make_candidate_card(estimated_monthly_cost=-10.0)

    @pytest.mark.parametrize(
        "field",
        ["name", "role", "department", "rationale"],
    )
    def test_blank_string_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            make_candidate_card(**{field: "  "})  # type: ignore[arg-type]


# ── HiringRequest ──────────────────────────────────────────────


@pytest.mark.unit
class TestHiringRequest:
    """HiringRequest construction and validators."""

    def test_construction_default_status(self) -> None:
        req = make_hiring_request()
        assert req.status == HiringRequestStatus.PENDING
        assert req.candidates == ()
        assert req.selected_candidate_id is None
        assert req.approval_id is None

    def test_frozen(self) -> None:
        req = make_hiring_request()
        with pytest.raises(ValidationError):
            req.status = HiringRequestStatus.APPROVED  # type: ignore[misc]

    def test_instantiated_without_candidate_raises(self) -> None:
        with pytest.raises(
            ValidationError,
            match="instantiated requests must have a selected_candidate_id",
        ):
            make_hiring_request(
                status=HiringRequestStatus.INSTANTIATED,
                selected_candidate_id=None,
            )

    def test_instantiated_with_candidate_ok(self) -> None:
        card = make_candidate_card(candidate_id="cand-001")
        req = make_hiring_request(
            status=HiringRequestStatus.INSTANTIATED,
            selected_candidate_id="cand-001",
            candidates=(card,),
        )
        assert req.status == HiringRequestStatus.INSTANTIATED
        assert req.selected_candidate_id == "cand-001"

    def test_pending_without_candidate_ok(self) -> None:
        req = make_hiring_request(
            status=HiringRequestStatus.PENDING,
            selected_candidate_id=None,
        )
        assert req.selected_candidate_id is None

    def test_approved_without_candidate_raises(self) -> None:
        """APPROVED status requires selected_candidate_id."""
        with pytest.raises(
            ValidationError,
            match="approved requests must have a selected_candidate_id",
        ):
            make_hiring_request(
                status=HiringRequestStatus.APPROVED,
                selected_candidate_id=None,
            )

    def test_approved_with_candidate_ok(self) -> None:
        """APPROVED status with selected_candidate_id passes."""
        card = make_candidate_card(candidate_id="cand-001")
        req = make_hiring_request(
            status=HiringRequestStatus.APPROVED,
            selected_candidate_id="cand-001",
            candidates=(card,),
        )
        assert req.status == HiringRequestStatus.APPROVED
        assert req.selected_candidate_id == "cand-001"

    def test_budget_limit_non_negative(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal"):
            make_hiring_request(budget_limit_monthly=-5.0)

    @pytest.mark.parametrize(
        "field",
        ["requested_by", "department", "role", "reason"],
    )
    def test_blank_required_fields_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            make_hiring_request(**{field: "  "})  # type: ignore[arg-type]


# ── FiringRequest ──────────────────────────────────────────────


@pytest.mark.unit
class TestFiringRequest:
    """FiringRequest construction and constraints."""

    def test_construction(self) -> None:
        req = make_firing_request()
        assert req.agent_id == "agent-001"
        assert req.reason == FiringReason.MANUAL
        assert req.completed_at is None

    def test_frozen(self) -> None:
        req = make_firing_request()
        with pytest.raises(ValidationError):
            req.reason = FiringReason.BUDGET  # type: ignore[misc]

    def test_defaults(self) -> None:
        req = make_firing_request()
        assert req.details == ""

    @pytest.mark.parametrize("reason", list(FiringReason))
    def test_all_reasons_accepted(self, reason: FiringReason) -> None:
        req = make_firing_request(reason=reason)
        assert req.reason == reason

    def test_completed_at_before_created_at_raises(self) -> None:
        """completed_at before created_at is rejected."""
        now = datetime.now(UTC)
        before = now - timedelta(hours=1)
        from synthorg.hr.models import FiringRequest

        with pytest.raises(ValidationError, match="completed_at"):
            FiringRequest(
                agent_id="agent-001",
                agent_name="test-agent",
                reason=FiringReason.MANUAL,
                requested_by="cto",
                created_at=now,
                completed_at=before,
            )


# ── OnboardingStepRecord ───────────────────────────────────────


@pytest.mark.unit
class TestOnboardingStepRecord:
    """OnboardingStepRecord construction."""

    def test_construction_incomplete(self) -> None:
        rec = OnboardingStepRecord(step=OnboardingStep.COMPANY_CONTEXT)
        assert rec.step == OnboardingStep.COMPANY_CONTEXT
        assert rec.completed is False
        assert rec.completed_at is None
        assert rec.notes == ""

    def test_construction_complete(self) -> None:
        now = datetime.now(UTC)
        rec = OnboardingStepRecord(
            step=OnboardingStep.PROJECT_BRIEFING,
            completed=True,
            completed_at=now,
            notes="Briefed on project X",
        )
        assert rec.completed is True
        assert rec.completed_at == now

    def test_frozen(self) -> None:
        rec = OnboardingStepRecord(step=OnboardingStep.TEAM_INTRODUCTIONS)
        with pytest.raises(ValidationError):
            rec.completed = True  # type: ignore[misc]

    def test_completed_true_without_completed_at_raises(self) -> None:
        """completed=True with completed_at=None is rejected."""
        with pytest.raises(ValidationError, match="completed_at"):
            OnboardingStepRecord(
                step=OnboardingStep.COMPANY_CONTEXT,
                completed=True,
                completed_at=None,
            )

    def test_completed_false_with_completed_at_raises(self) -> None:
        """completed=False with a non-None completed_at is rejected."""
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="completed_at"):
            OnboardingStepRecord(
                step=OnboardingStep.COMPANY_CONTEXT,
                completed=False,
                completed_at=now,
            )


# ── OnboardingChecklist ────────────────────────────────────────


@pytest.mark.unit
class TestOnboardingChecklist:
    """OnboardingChecklist construction and is_complete computed field."""

    def test_all_incomplete(self) -> None:
        steps = tuple(OnboardingStepRecord(step=s) for s in OnboardingStep)
        checklist = OnboardingChecklist(
            agent_id="agent-001",
            steps=steps,
            started_at=datetime.now(UTC),
        )
        assert checklist.is_complete is False
        assert checklist.completed_at is None

    def test_all_complete(self) -> None:
        now = datetime.now(UTC)
        steps = tuple(
            OnboardingStepRecord(step=s, completed=True, completed_at=now)
            for s in OnboardingStep
        )
        checklist = OnboardingChecklist(
            agent_id="agent-001",
            steps=steps,
            started_at=now,
            completed_at=now,
        )
        assert checklist.is_complete is True

    def test_partial_complete(self) -> None:
        now = datetime.now(UTC)
        steps = (
            OnboardingStepRecord(
                step=OnboardingStep.COMPANY_CONTEXT,
                completed=True,
                completed_at=now,
            ),
            OnboardingStepRecord(step=OnboardingStep.PROJECT_BRIEFING),
            OnboardingStepRecord(step=OnboardingStep.TEAM_INTRODUCTIONS),
        )
        checklist = OnboardingChecklist(
            agent_id="agent-001",
            steps=steps,
            started_at=now,
        )
        assert checklist.is_complete is False

    def test_frozen(self) -> None:
        steps = tuple(OnboardingStepRecord(step=s) for s in OnboardingStep)
        checklist = OnboardingChecklist(
            agent_id="agent-001",
            steps=steps,
            started_at=datetime.now(UTC),
        )
        with pytest.raises(ValidationError):
            checklist.agent_id = "other"  # type: ignore[misc]


# ── OffboardingRecord ──────────────────────────────────────────


@pytest.mark.unit
class TestOffboardingRecord:
    """OffboardingRecord construction and temporal validation."""

    def test_construction(self) -> None:
        now = datetime.now(UTC)
        record = OffboardingRecord(
            agent_id="agent-001",
            agent_name="test-agent",
            firing_request_id="fire-001",
            started_at=now,
            completed_at=now,
        )
        assert record.tasks_reassigned == ()
        assert record.memory_archive_id is None
        assert record.org_memories_promoted == 0
        assert record.team_notification_sent is False

    def test_temporal_order_valid(self) -> None:
        start = datetime.now(UTC)
        end = start + timedelta(seconds=10)
        record = OffboardingRecord(
            agent_id="agent-001",
            agent_name="test-agent",
            firing_request_id="fire-001",
            started_at=start,
            completed_at=end,
        )
        assert record.completed_at >= record.started_at

    def test_temporal_order_equal_ok(self) -> None:
        now = datetime.now(UTC)
        record = OffboardingRecord(
            agent_id="agent-001",
            agent_name="test-agent",
            firing_request_id="fire-001",
            started_at=now,
            completed_at=now,
        )
        assert record.completed_at == record.started_at

    def test_temporal_order_reversed_raises(self) -> None:
        now = datetime.now(UTC)
        before = now - timedelta(seconds=10)
        with pytest.raises(ValidationError, match="completed_at"):
            OffboardingRecord(
                agent_id="agent-001",
                agent_name="test-agent",
                firing_request_id="fire-001",
                started_at=now,
                completed_at=before,
            )

    def test_frozen(self) -> None:
        now = datetime.now(UTC)
        record = OffboardingRecord(
            agent_id="agent-001",
            agent_name="test-agent",
            firing_request_id="fire-001",
            started_at=now,
            completed_at=now,
        )
        with pytest.raises(ValidationError):
            record.agent_id = "other"  # type: ignore[misc]


# ── AgentLifecycleEvent ───────────────────────────────────────


@pytest.mark.unit
class TestAgentLifecycleEvent:
    """AgentLifecycleEvent construction and constraints."""

    def test_construction_minimal(self) -> None:
        now = datetime.now(UTC)
        event = AgentLifecycleEvent(
            agent_id="agent-001",
            agent_name="test-agent",
            event_type=LifecycleEventType.HIRED,
            timestamp=now,
            initiated_by="hr-system",
        )
        assert event.agent_id == "agent-001"
        assert event.event_type == LifecycleEventType.HIRED
        assert event.details == ""
        assert event.metadata == {}

    def test_construction_with_metadata(self) -> None:
        now = datetime.now(UTC)
        event = AgentLifecycleEvent(
            agent_id="agent-001",
            agent_name="test-agent",
            event_type=LifecycleEventType.FIRED,
            timestamp=now,
            initiated_by="cto",
            details="Performance issues",
            metadata={"reason": "performance", "department": "engineering"},
        )
        assert event.metadata["reason"] == "performance"
        assert event.details == "Performance issues"

    def test_frozen(self) -> None:
        now = datetime.now(UTC)
        event = AgentLifecycleEvent(
            agent_id="agent-001",
            agent_name="test-agent",
            event_type=LifecycleEventType.ONBOARDED,
            timestamp=now,
            initiated_by="hr-system",
        )
        with pytest.raises(ValidationError):
            event.event_type = LifecycleEventType.FIRED  # type: ignore[misc]

    def test_auto_generated_id(self) -> None:
        now = datetime.now(UTC)
        event_a = AgentLifecycleEvent(
            agent_id="agent-001",
            agent_name="test-agent",
            event_type=LifecycleEventType.HIRED,
            timestamp=now,
            initiated_by="hr-system",
        )
        event_b = AgentLifecycleEvent(
            agent_id="agent-001",
            agent_name="test-agent",
            event_type=LifecycleEventType.HIRED,
            timestamp=now,
            initiated_by="hr-system",
        )
        assert event_a.id != event_b.id

    @pytest.mark.parametrize("event_type", list(LifecycleEventType))
    def test_all_event_types_accepted(
        self,
        event_type: LifecycleEventType,
    ) -> None:
        now = datetime.now(UTC)
        event = AgentLifecycleEvent(
            agent_id="agent-001",
            agent_name="test-agent",
            event_type=event_type,
            timestamp=now,
            initiated_by="hr-system",
        )
        assert event.event_type == event_type

    @pytest.mark.parametrize(
        "field",
        ["agent_id", "agent_name", "initiated_by"],
    )
    def test_blank_fields_rejected(self, field: str) -> None:
        now = datetime.now(UTC)
        kwargs: dict[str, Any] = {
            "agent_id": "agent-001",
            "agent_name": "test-agent",
            "event_type": LifecycleEventType.HIRED,
            "timestamp": now,
            "initiated_by": "hr-system",
        }
        kwargs[field] = "  "
        with pytest.raises(ValidationError):
            AgentLifecycleEvent(**kwargs)
