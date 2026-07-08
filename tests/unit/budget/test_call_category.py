"""Tests for LLM call categorization enums."""

import pytest

from synthorg.budget.call_category import LLMCallCategory, OrchestrationAlertLevel


@pytest.mark.unit
class TestLLMCallCategory:
    """LLMCallCategory enum values."""

    def test_values(self) -> None:
        assert LLMCallCategory.PRODUCTIVE.value == "productive"
        assert LLMCallCategory.COORDINATION.value == "coordination"
        assert LLMCallCategory.SYSTEM.value == "system"

    def test_member_count(self) -> None:
        assert len(LLMCallCategory) == 5

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (LLMCallCategory.PRODUCTIVE, "productive"),
            (LLMCallCategory.COORDINATION, "coordination"),
            (LLMCallCategory.SYSTEM, "system"),
            (LLMCallCategory.EMBEDDING, "embedding"),
            (LLMCallCategory.IMAGE_GENERATION, "image_generation"),
        ],
    )
    def test_string_conversion(
        self,
        member: LLMCallCategory,
        expected: str,
    ) -> None:
        assert str(member) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("productive", LLMCallCategory.PRODUCTIVE),
            ("coordination", LLMCallCategory.COORDINATION),
            ("system", LLMCallCategory.SYSTEM),
            ("embedding", LLMCallCategory.EMBEDDING),
            ("image_generation", LLMCallCategory.IMAGE_GENERATION),
        ],
    )
    def test_from_string(
        self,
        value: str,
        expected: LLMCallCategory,
    ) -> None:
        assert LLMCallCategory(value) == expected


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
