"""Tests for LLM call categorization enums."""

import pytest

from synthorg.budget.call_category import LLMCallCategory, OrchestrationAlertLevel

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestLLMCallCategory:
    """LLMCallCategory enum values."""

    def test_values(self) -> None:
        assert LLMCallCategory.PRODUCTIVE.value == "productive"
        assert LLMCallCategory.COORDINATION.value == "coordination"
        assert LLMCallCategory.SYSTEM.value == "system"

    def test_member_count(self) -> None:
        assert len(LLMCallCategory) == 3

    def test_string_conversion(self) -> None:
        assert str(LLMCallCategory.PRODUCTIVE) == "productive"
        assert str(LLMCallCategory.COORDINATION) == "coordination"
        assert str(LLMCallCategory.SYSTEM) == "system"

    def test_from_string(self) -> None:
        assert LLMCallCategory("productive") == LLMCallCategory.PRODUCTIVE
        assert LLMCallCategory("coordination") == LLMCallCategory.COORDINATION
        assert LLMCallCategory("system") == LLMCallCategory.SYSTEM


@pytest.mark.unit
class TestOrchestrationAlertLevel:
    """OrchestrationAlertLevel enum values."""

    def test_values(self) -> None:
        assert OrchestrationAlertLevel.NORMAL.value == "normal"
        assert OrchestrationAlertLevel.INFO.value == "info"
        assert OrchestrationAlertLevel.WARNING.value == "warning"
        assert OrchestrationAlertLevel.CRITICAL.value == "critical"

    def test_member_count(self) -> None:
        assert len(OrchestrationAlertLevel) == 4

    def test_string_conversion(self) -> None:
        assert str(OrchestrationAlertLevel.NORMAL) == "normal"
        assert str(OrchestrationAlertLevel.CRITICAL) == "critical"
