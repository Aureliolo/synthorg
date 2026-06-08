"""Unit tests for FailureCategory and DecisionOutcome enums."""

import pytest

from synthorg.engine.decisions import DecisionOutcome
from synthorg.engine.recovery import FailureCategory


@pytest.mark.unit
class TestFailureCategory:
    """Tests for the FailureCategory enum."""

    def test_members(self) -> None:
        """All expected members exist."""
        assert FailureCategory.TOOL_FAILURE.value == "tool_failure"
        assert FailureCategory.STAGNATION.value == "stagnation"
        assert FailureCategory.BUDGET_EXCEEDED.value == "budget_exceeded"
        assert FailureCategory.QUALITY_GATE_FAILED.value == "quality_gate_failed"
        assert FailureCategory.TIMEOUT.value == "timeout"
        assert FailureCategory.DELEGATION_FAILED.value == "delegation_failed"
        assert FailureCategory.UNKNOWN.value == "unknown"

    def test_member_count(self) -> None:
        """Exactly 7 members."""
        assert len(FailureCategory) == 7

    @pytest.mark.parametrize(
        "value",
        [
            "tool_failure",
            "stagnation",
            "budget_exceeded",
            "quality_gate_failed",
            "timeout",
            "delegation_failed",
            "unknown",
        ],
    )
    def test_round_trip(self, value: str) -> None:
        """Each value round-trips through construction."""
        assert FailureCategory(value).value == value


@pytest.mark.unit
class TestDecisionOutcome:
    """Tests for the DecisionOutcome enum."""

    def test_members(self) -> None:
        """All expected members exist."""
        assert DecisionOutcome.APPROVED.value == "approved"
        assert DecisionOutcome.REJECTED.value == "rejected"
        assert DecisionOutcome.AUTO_APPROVED.value == "auto_approved"
        assert DecisionOutcome.AUTO_REJECTED.value == "auto_rejected"
        assert DecisionOutcome.ESCALATED.value == "escalated"

    def test_member_count(self) -> None:
        """Exactly 5 members."""
        assert len(DecisionOutcome) == 5

    @pytest.mark.parametrize(
        "value",
        [
            "approved",
            "rejected",
            "auto_approved",
            "auto_rejected",
            "escalated",
        ],
    )
    def test_round_trip(self, value: str) -> None:
        """Each value round-trips through construction."""
        assert DecisionOutcome(value).value == value
