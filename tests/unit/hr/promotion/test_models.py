"""Unit tests for promotion domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ApprovalStatus, SeniorityLevel
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.promotion.models import (
    CriterionResult,
    PromotionApprovalDecision,
    PromotionEvaluation,
    PromotionRecord,
    PromotionRequest,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.mark.unit
class TestCriterionResult:
    """Tests for CriterionResult model."""

    def test_creation_with_required_fields(self) -> None:
        """CriterionResult can be created with required fields."""
        result = CriterionResult(
            name="quality_score",
            met=True,
            current_value=8.5,
            threshold=7.0,
        )
        assert result.name == "quality_score"
        assert result.met is True
        assert result.current_value == 8.5
        assert result.threshold == 7.0
        assert result.weight is None

    def test_creation_with_weight(self) -> None:
        """CriterionResult accepts optional weight."""
        result = CriterionResult(
            name="success_rate",
            met=False,
            current_value=0.6,
            threshold=0.8,
            weight=0.5,
        )
        assert result.weight == 0.5

    def test_frozen(self) -> None:
        """CriterionResult is immutable."""
        result = CriterionResult(
            name="quality_score",
            met=True,
            current_value=8.0,
            threshold=7.0,
        )
        with pytest.raises(ValidationError):
            result.met = False  # type: ignore[misc]

    def test_weight_bounds(self) -> None:
        """Weight must be between 0.0 and 1.0."""
        with pytest.raises(ValidationError):
            CriterionResult(
                name="quality_score",
                met=True,
                current_value=8.0,
                threshold=7.0,
                weight=1.5,
            )


@pytest.mark.unit
class TestPromotionEvaluation:
    """Tests for PromotionEvaluation model."""

    def test_criteria_met_count_computed(self) -> None:
        """criteria_met_count is computed from criteria_results."""
        criteria = (
            CriterionResult(
                name="quality_score",
                met=True,
                current_value=8.0,
                threshold=7.0,
            ),
            CriterionResult(
                name="success_rate",
                met=True,
                current_value=0.9,
                threshold=0.8,
            ),
            CriterionResult(
                name="tasks_completed",
                met=False,
                current_value=5.0,
                threshold=10.0,
            ),
        )
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=criteria,
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        assert evaluation.criteria_met_count == 2

    def test_zero_criteria_met(self) -> None:
        """criteria_met_count is 0 when no criteria met."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.JUNIOR,
            direction=PromotionDirection.DEMOTION,
            criteria_results=(),
            required_criteria_met=False,
            eligible=False,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        assert evaluation.criteria_met_count == 0

    def test_frozen(self) -> None:
        """PromotionEvaluation is immutable."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        with pytest.raises(ValidationError):
            evaluation.eligible = False  # type: ignore[misc]


@pytest.mark.unit
class TestPromotionApprovalDecision:
    """Tests for PromotionApprovalDecision model."""

    def test_creation(self) -> None:
        """PromotionApprovalDecision can be created."""
        decision = PromotionApprovalDecision(
            auto_approve=True,
            reason="Below threshold",
        )
        assert decision.auto_approve is True
        assert decision.requires_human is False
        assert decision.reason == "Below threshold"

    def test_requires_human_is_inverse_of_auto_approve(self) -> None:
        """requires_human is a computed inverse of auto_approve."""
        auto = PromotionApprovalDecision(
            auto_approve=True,
            reason="Auto",
        )
        assert auto.requires_human is False
        manual = PromotionApprovalDecision(
            auto_approve=False,
            reason="Manual",
        )
        assert manual.requires_human is True

    def test_frozen(self) -> None:
        """PromotionApprovalDecision is immutable."""
        decision = PromotionApprovalDecision(
            auto_approve=True,
            reason="Auto-approved",
        )
        with pytest.raises(ValidationError):
            decision.auto_approve = False  # type: ignore[misc]


@pytest.mark.unit
class TestPromotionRecord:
    """Tests for PromotionRecord model."""

    def test_creation_with_defaults(self) -> None:
        """PromotionRecord has sensible defaults."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        record = PromotionRecord(
            agent_id="agent-001",
            agent_name="test-agent",
            old_level=SeniorityLevel.JUNIOR,
            new_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            evaluation=evaluation,
            effective_at=datetime.now(UTC),
            initiated_by="system",
        )
        assert record.id  # auto-generated UUID
        assert record.approved_by is None
        assert record.approval_id is None
        assert record.model_changed is False
        assert record.old_model_id is None
        assert record.new_model_id is None

    def test_frozen(self) -> None:
        """PromotionRecord is immutable."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        record = PromotionRecord(
            agent_id="agent-001",
            agent_name="test-agent",
            old_level=SeniorityLevel.JUNIOR,
            new_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            evaluation=evaluation,
            effective_at=datetime.now(UTC),
            initiated_by="system",
        )
        with pytest.raises(ValidationError):
            record.model_changed = True  # type: ignore[misc]

    def test_model_changed_true_missing_old_model_id_raises(self) -> None:
        """model_changed=True with old_model_id=None raises ValidationError."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        with pytest.raises(ValidationError, match="old_model_id and new_model_id"):
            PromotionRecord(
                agent_id="agent-001",
                agent_name="test-agent",
                old_level=SeniorityLevel.JUNIOR,
                new_level=SeniorityLevel.MID,
                direction=PromotionDirection.PROMOTION,
                evaluation=evaluation,
                effective_at=datetime.now(UTC),
                initiated_by="system",
                model_changed=True,
                old_model_id=None,
                new_model_id="test-large-001",
            )

    def test_model_changed_true_missing_new_model_id_raises(self) -> None:
        """model_changed=True with new_model_id=None raises ValidationError."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        with pytest.raises(ValidationError, match="old_model_id and new_model_id"):
            PromotionRecord(
                agent_id="agent-001",
                agent_name="test-agent",
                old_level=SeniorityLevel.JUNIOR,
                new_level=SeniorityLevel.MID,
                direction=PromotionDirection.PROMOTION,
                evaluation=evaluation,
                effective_at=datetime.now(UTC),
                initiated_by="system",
                model_changed=True,
                old_model_id="test-small-001",
                new_model_id=None,
            )

    def test_model_changed_false_with_old_model_id_raises(self) -> None:
        """model_changed=False with old_model_id set raises ValidationError."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        with pytest.raises(ValidationError, match="model IDs to be None"):
            PromotionRecord(
                agent_id="agent-001",
                agent_name="test-agent",
                old_level=SeniorityLevel.JUNIOR,
                new_level=SeniorityLevel.MID,
                direction=PromotionDirection.PROMOTION,
                evaluation=evaluation,
                effective_at=datetime.now(UTC),
                initiated_by="system",
                model_changed=False,
                old_model_id="test-small-001",
            )

    def test_model_changed_true_same_model_ids_raises(self) -> None:
        """model_changed=True with identical model IDs raises ValidationError."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        with pytest.raises(
            ValidationError,
            match="old_model_id and new_model_id to differ",
        ):
            PromotionRecord(
                agent_id="agent-001",
                agent_name="test-agent",
                old_level=SeniorityLevel.JUNIOR,
                new_level=SeniorityLevel.MID,
                direction=PromotionDirection.PROMOTION,
                evaluation=evaluation,
                effective_at=datetime.now(UTC),
                initiated_by="system",
                model_changed=True,
                old_model_id="test-small-001",
                new_model_id="test-small-001",
            )

    def test_model_changed_true_different_ids_succeeds(self) -> None:
        """model_changed=True with different model IDs succeeds."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        record = PromotionRecord(
            agent_id="agent-001",
            agent_name="test-agent",
            old_level=SeniorityLevel.JUNIOR,
            new_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            evaluation=evaluation,
            effective_at=datetime.now(UTC),
            initiated_by="system",
            model_changed=True,
            old_model_id="test-small-001",
            new_model_id="test-large-001",
        )
        assert record.model_changed is True
        assert record.old_model_id == "test-small-001"
        assert record.new_model_id == "test-large-001"


@pytest.mark.unit
class TestPromotionEvaluationDirectionConsistency:
    """Tests for PromotionEvaluation._validate_direction_consistency."""

    def test_promotion_with_lower_target_raises(self) -> None:
        """direction=PROMOTION with target_level < current_level raises."""
        with pytest.raises(
            ValidationError,
            match="target_level > current_level",
        ):
            PromotionEvaluation(
                agent_id="agent-001",
                current_level=SeniorityLevel.SENIOR,
                target_level=SeniorityLevel.JUNIOR,
                direction=PromotionDirection.PROMOTION,
                criteria_results=(),
                required_criteria_met=True,
                eligible=True,
                evaluated_at=datetime.now(UTC),
                strategy_name="threshold_evaluator",
            )

    def test_demotion_with_higher_target_raises(self) -> None:
        """direction=DEMOTION with target_level > current_level raises."""
        with pytest.raises(
            ValidationError,
            match="target_level < current_level",
        ):
            PromotionEvaluation(
                agent_id="agent-001",
                current_level=SeniorityLevel.JUNIOR,
                target_level=SeniorityLevel.SENIOR,
                direction=PromotionDirection.DEMOTION,
                criteria_results=(),
                required_criteria_met=True,
                eligible=True,
                evaluated_at=datetime.now(UTC),
                strategy_name="threshold_evaluator",
            )


@pytest.mark.unit
class TestPromotionRequest:
    """Tests for PromotionRequest model."""

    def test_default_status_is_pending(self) -> None:
        """PromotionRequest defaults to PENDING status."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        request = PromotionRequest(
            agent_id="agent-001",
            agent_name="test-agent",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            evaluation=evaluation,
            created_at=datetime.now(UTC),
        )
        assert request.status == ApprovalStatus.PENDING
        assert request.id  # auto-generated UUID
        assert request.approval_id is None

    def test_frozen(self) -> None:
        """PromotionRequest is immutable."""
        evaluation = PromotionEvaluation(
            agent_id="agent-001",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            criteria_results=(),
            required_criteria_met=True,
            eligible=True,
            evaluated_at=datetime.now(UTC),
            strategy_name="threshold_evaluator",
        )
        request = PromotionRequest(
            agent_id="agent-001",
            agent_name="test-agent",
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
            evaluation=evaluation,
            created_at=datetime.now(UTC),
        )
        with pytest.raises(ValidationError):
            request.status = ApprovalStatus.APPROVED  # type: ignore[misc]
