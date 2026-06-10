"""Tests for scaling domain models."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import (
    ScalingActionType,
    ScalingOutcome,
    ScalingStrategyName,
)
from synthorg.hr.scaling.models import (
    ScalingActionRecord,
    ScalingSignal,
)

from .conftest import NOW, make_context, make_decision, make_signal

# -- ScalingSignal -----------------------------------------------------------


@pytest.mark.unit
class TestScalingSignal:
    """ScalingSignal construction, frozen enforcement, validation."""

    def test_valid_signal(self) -> None:
        signal = make_signal(
            name="avg_utilization",
            value=0.87,
            threshold=0.85,
        )
        assert signal.name == "avg_utilization"
        assert signal.value == 0.87
        assert signal.threshold == 0.85
        assert signal.source == "workload"

    def test_signal_without_threshold(self) -> None:
        signal = make_signal(threshold=None)
        assert signal.threshold is None

    def test_frozen_enforcement(self) -> None:
        signal = make_signal()
        with pytest.raises(ValidationError):
            signal.value = 0.5  # type: ignore[misc]

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScalingSignal(
                name=NotBlankStr(""),
                value=0.5,
                source=NotBlankStr("test"),
                timestamp=NOW,
            )

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScalingSignal(
                name=NotBlankStr("   "),
                value=0.5,
                source=NotBlankStr("test"),
                timestamp=NOW,
            )

    def test_nan_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScalingSignal(
                name=NotBlankStr("test"),
                value=float("nan"),
                source=NotBlankStr("test"),
                timestamp=NOW,
            )

    def test_inf_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScalingSignal(
                name=NotBlankStr("test"),
                value=float("inf"),
                source=NotBlankStr("test"),
                timestamp=NOW,
            )


# -- ScalingContext ----------------------------------------------------------


@pytest.mark.unit
class TestScalingContext:
    """ScalingContext construction, frozen enforcement, validation."""

    def test_valid_context(self) -> None:
        ctx = make_context(agent_ids=("a", "b", "c"))
        assert ctx.active_agent_count == 3
        assert len(ctx.agent_ids) == 3

    def test_empty_context(self) -> None:
        ctx = make_context(agent_ids=())
        assert ctx.active_agent_count == 0
        assert ctx.agent_ids == ()
        assert ctx.workload_signals == ()

    def test_context_with_signals(self) -> None:
        signals = (make_signal(), make_signal(name="peak_utilization"))
        ctx = make_context(workload_signals=signals)
        assert len(ctx.workload_signals) == 2

    def test_frozen_enforcement(self) -> None:
        ctx = make_context()
        with pytest.raises(ValidationError):
            ctx.agent_ids = ()  # type: ignore[misc]

    def test_active_agent_count_derives_from_agent_ids(self) -> None:
        ctx = make_context(agent_ids=("a", "b"))
        assert ctx.active_agent_count == len(ctx.agent_ids) == 2


# -- ScalingDecision ---------------------------------------------------------


@pytest.mark.unit
class TestScalingDecision:
    """ScalingDecision construction, validation, target field invariants."""

    def test_valid_hire_decision(self) -> None:
        decision = make_decision(
            action_type=ScalingActionType.HIRE,
            target_role="backend_developer",
        )
        assert decision.action_type == ScalingActionType.HIRE
        assert decision.target_role == "backend_developer"
        assert isinstance(decision.id, UUID)

    def test_valid_prune_decision(self) -> None:
        decision = make_decision(
            action_type=ScalingActionType.PRUNE,
            target_agent_id="agent-001",
            target_role=None,
        )
        assert decision.action_type == ScalingActionType.PRUNE
        assert decision.target_agent_id == "agent-001"

    def test_valid_hold_decision(self) -> None:
        decision = make_decision(
            action_type=ScalingActionType.HOLD,
            target_role=None,
        )
        assert decision.action_type == ScalingActionType.HOLD

    def test_valid_noop_decision(self) -> None:
        decision = make_decision(
            action_type=ScalingActionType.NO_OP,
            target_role=None,
        )
        assert decision.action_type == ScalingActionType.NO_OP

    def test_hire_without_role_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="HIRE decisions must specify target_role",
        ):
            make_decision(
                action_type=ScalingActionType.HIRE,
                target_role=None,
            )

    def test_prune_without_agent_id_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="PRUNE decisions must specify target_agent_id",
        ):
            make_decision(
                action_type=ScalingActionType.PRUNE,
                target_agent_id=None,
                target_role=None,
            )

    def test_frozen_enforcement(self) -> None:
        decision = make_decision()
        with pytest.raises(ValidationError):
            decision.confidence = 0.5  # type: ignore[misc]

    def test_confidence_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_decision(confidence=-0.1)

    def test_confidence_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_decision(confidence=1.1)

    def test_confidence_boundary_zero(self) -> None:
        decision = make_decision(confidence=0.0)
        assert decision.confidence == 0.0

    def test_confidence_boundary_one(self) -> None:
        decision = make_decision(confidence=1.0)
        assert decision.confidence == 1.0

    def test_decision_with_signals(self) -> None:
        signals = (make_signal(),)
        decision = make_decision(signals=signals)
        assert len(decision.signals) == 1

    def test_decision_with_skills(self) -> None:
        decision = make_decision(target_skills=("python", "litestar"))
        assert decision.target_skills == ("python", "litestar")

    @pytest.mark.parametrize(
        "strategy",
        list(ScalingStrategyName),
        ids=lambda s: s.value,
    )
    def test_all_strategy_names_accepted(
        self,
        strategy: ScalingStrategyName,
    ) -> None:
        decision = make_decision(source_strategy=strategy)
        assert decision.source_strategy == strategy


# -- ScalingActionRecord -----------------------------------------------------


@pytest.mark.unit
class TestScalingActionRecord:
    """ScalingActionRecord construction and validation."""

    def test_valid_executed_record(self) -> None:
        record = ScalingActionRecord(
            decision_id=NotBlankStr("decision-001"),
            outcome=ScalingOutcome.EXECUTED,
            result_id=NotBlankStr("hire-request-001"),
            executed_at=NOW,
        )
        assert record.outcome == ScalingOutcome.EXECUTED
        assert record.result_id == "hire-request-001"
        assert isinstance(record.id, UUID)

    def test_valid_deferred_record(self) -> None:
        record = ScalingActionRecord(
            decision_id=NotBlankStr("decision-002"),
            outcome=ScalingOutcome.DEFERRED,
            result_id=NotBlankStr("approval-item-001"),
            reason=NotBlankStr("awaiting human approval"),
            executed_at=NOW,
        )
        assert record.outcome == ScalingOutcome.DEFERRED
        assert record.reason == "awaiting human approval"

    def test_valid_failed_record(self) -> None:
        record = ScalingActionRecord(
            decision_id=NotBlankStr("decision-003"),
            outcome=ScalingOutcome.FAILED,
            reason=NotBlankStr("hiring service unavailable"),
            executed_at=NOW,
        )
        assert record.outcome == ScalingOutcome.FAILED
        assert record.result_id is None

    def test_frozen_enforcement(self) -> None:
        record = ScalingActionRecord(
            decision_id=NotBlankStr("decision-001"),
            outcome=ScalingOutcome.EXECUTED,
            result_id=NotBlankStr("result-001"),
            executed_at=NOW,
        )
        with pytest.raises(ValidationError):
            record.outcome = ScalingOutcome.FAILED  # type: ignore[misc]

    def test_rejected_record(self) -> None:
        record = ScalingActionRecord(
            decision_id=NotBlankStr("decision-001"),
            outcome=ScalingOutcome.REJECTED,
            reason=NotBlankStr("approval timeout"),
            executed_at=NOW,
        )
        assert record.outcome == ScalingOutcome.REJECTED

    def test_rejected_without_reason_rejected(self) -> None:
        with pytest.raises(ValidationError, match="REJECTED"):
            ScalingActionRecord(
                decision_id=NotBlankStr("decision-001"),
                outcome=ScalingOutcome.REJECTED,
                executed_at=NOW,
            )

    def test_failed_without_reason_rejected(self) -> None:
        with pytest.raises(ValidationError, match="FAILED"):
            ScalingActionRecord(
                decision_id=NotBlankStr("decision-001"),
                outcome=ScalingOutcome.FAILED,
                executed_at=NOW,
            )

    def test_executed_without_result_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="executed"):
            ScalingActionRecord(
                decision_id=NotBlankStr("decision-001"),
                outcome=ScalingOutcome.EXECUTED,
                executed_at=NOW,
            )
