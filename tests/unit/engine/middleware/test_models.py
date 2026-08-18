"""Tests for middleware domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.engine.middleware.errors import (
    ClarificationRequiredError,
    MiddlewareConfigError,
    MiddlewareError,
    MiddlewareRegistryError,
)
from synthorg.engine.middleware.models import (
    AssumptionViolationEvent,
    AssumptionViolationType,
    ModelCallResult,
    TaskLedger,
    ToolCallResult,
)
from synthorg.providers.models import TokenUsage

# ── ModelCallResult ───────────────────────────────────────────────


@pytest.mark.unit
class TestModelCallResult:
    """ModelCallResult frozen model."""

    def test_valid(self) -> None:
        result = ModelCallResult(
            response_text="hello",
            token_usage=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cost=0.01,
            ),
            finish_reason="stop",
        )
        assert result.response_text == "hello"
        assert result.finish_reason == "stop"
        assert result.error is None

    def test_frozen(self) -> None:
        result = ModelCallResult(
            response_text="hello",
            token_usage=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cost=0.01,
            ),
            finish_reason="stop",
        )
        with pytest.raises(ValidationError):
            result.response_text = "other"  # type: ignore[misc]


# ── ToolCallResult ────────────────────────────────────────────────


@pytest.mark.unit
class TestToolCallResult:
    """ToolCallResult frozen model with error consistency."""

    def test_valid_success(self) -> None:
        result = ToolCallResult(
            tool_name="my_tool",
            output="done",
        )
        assert result.success is True
        assert result.error is None

    def test_valid_failure(self) -> None:
        result = ToolCallResult(
            tool_name="my_tool",
            success=False,
            error="tool broke",
        )
        assert result.success is False
        assert result.error == "tool broke"

    def test_rejects_success_with_error(self) -> None:
        with pytest.raises(
            ValidationError,
            match="successful tool call",
        ):
            ToolCallResult(
                tool_name="t",
                success=True,
                error="should not exist",
            )

    def test_rejects_failure_without_error(self) -> None:
        with pytest.raises(
            ValidationError,
            match="failed tool call",
        ):
            ToolCallResult(
                tool_name="t",
                success=False,
            )

    def test_frozen(self) -> None:
        result = ToolCallResult(tool_name="t", output="ok")
        with pytest.raises(ValidationError):
            result.tool_name = "other"  # type: ignore[misc]


# ── AssumptionViolationEvent ──────────────────────────────────────


@pytest.mark.unit
class TestAssumptionViolationEvent:
    """AssumptionViolationEvent model validation."""

    def test_valid(self) -> None:
        event = AssumptionViolationEvent(
            agent_id="agent-1",
            task_id="task-1",
            violation_type=AssumptionViolationType.PRECONDITION_CHANGED,
            description="Precondition X no longer holds",
            evidence="Model said: X has changed",
            turn_number=3,
        )
        assert event.violation_type == "precondition_changed"
        assert event.turn_number == 3

    def test_rejects_zero_turn(self) -> None:
        with pytest.raises(ValidationError):
            AssumptionViolationEvent(
                agent_id="a",
                task_id="t",
                violation_type=AssumptionViolationType.CRITERIA_CONFLICT,
                description="d",
                evidence="e",
                turn_number=0,
            )

    def test_rejects_blank_description(self) -> None:
        with pytest.raises(ValidationError):
            AssumptionViolationEvent(
                agent_id="a",
                task_id="t",
                violation_type=AssumptionViolationType.DEPENDENCY_FAILED,
                description="  ",
                evidence="e",
                turn_number=1,
            )

    def test_enum_values(self) -> None:
        assert len(AssumptionViolationType) == 3
        assert (
            AssumptionViolationType.PRECONDITION_CHANGED.value == "precondition_changed"
        )
        assert AssumptionViolationType.CRITERIA_CONFLICT.value == "criteria_conflict"
        assert AssumptionViolationType.DEPENDENCY_FAILED.value == "dependency_failed"


# ── TaskLedger ────────────────────────────────────────────────────


@pytest.mark.unit
class TestTaskLedger:
    """TaskLedger frozen model."""

    def test_valid(self) -> None:
        now = datetime.now(UTC)
        ledger = TaskLedger(
            plan_text="Do step 1, then step 2",
            known_facts=("fact-1",),
            plan_version=1,
            created_at=now,
        )
        assert ledger.plan_text == "Do step 1, then step 2"
        assert ledger.plan_version == 1
        assert ledger.superseded_at is None

    def test_rejects_zero_version(self) -> None:
        with pytest.raises(ValidationError):
            TaskLedger(
                plan_text="plan",
                plan_version=0,
                created_at=datetime.now(UTC),
            )

    def test_rejects_blank_plan_text(self) -> None:
        with pytest.raises(ValidationError):
            TaskLedger(
                plan_text="  ",
                created_at=datetime.now(UTC),
            )

    def test_frozen(self) -> None:
        ledger = TaskLedger(
            plan_text="plan",
            created_at=datetime.now(UTC),
        )
        with pytest.raises(ValidationError):
            ledger.plan_version = 2  # type: ignore[misc]


# ── Error hierarchy ───────────────────────────────────────────────


@pytest.mark.unit
class TestMiddlewareErrors:
    """Middleware exception hierarchy."""

    def test_middleware_error_is_engine_error(self) -> None:
        from synthorg.engine.errors import EngineError

        assert issubclass(MiddlewareError, EngineError)

    def test_config_error_hierarchy(self) -> None:
        assert issubclass(MiddlewareConfigError, MiddlewareError)

    def test_registry_error_hierarchy(self) -> None:
        assert issubclass(MiddlewareRegistryError, MiddlewareError)

    def test_clarification_error_hierarchy(self) -> None:
        assert issubclass(ClarificationRequiredError, MiddlewareError)

    def test_registry_error_message(self) -> None:
        err = MiddlewareRegistryError(
            "bad_name",
            registry_type="agent",
        )
        assert "bad_name" in str(err)
        assert err.middleware_name == "bad_name"
        assert err.registry_type == "agent"

    def test_clarification_error_message(self) -> None:
        err = ClarificationRequiredError(
            task_id="task-1",
            reasons=("too vague", "too short"),
        )
        assert "task-1" in str(err)
        assert err.task_id == "task-1"
        assert err.reasons == ("too vague", "too short")

    def test_clarification_error_overflow(self) -> None:
        reasons = tuple(f"reason-{i}" for i in range(8))
        err = ClarificationRequiredError(
            task_id="t",
            reasons=reasons,
        )
        assert "+3 more" in str(err)
