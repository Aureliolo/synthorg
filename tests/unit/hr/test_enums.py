"""Tests for HR domain enumerations."""

import pytest

from synthorg.hr.enums import (
    FiringReason,
    HiringRequestStatus,
    LifecycleEventType,
    OnboardingStep,
    PromotionDirection,
    TrendDirection,
)


@pytest.mark.unit
class TestHiringRequestStatus:
    """HiringRequestStatus enum values."""

    def test_values(self) -> None:
        assert HiringRequestStatus.PENDING.value == "pending"
        assert HiringRequestStatus.APPROVED.value == "approved"
        assert HiringRequestStatus.REJECTED.value == "rejected"
        assert HiringRequestStatus.INSTANTIATED.value == "instantiated"

    def test_completeness(self) -> None:
        assert len(HiringRequestStatus) == 4


@pytest.mark.unit
class TestFiringReason:
    """FiringReason enum values."""

    def test_values(self) -> None:
        assert FiringReason.MANUAL.value == "manual"
        assert FiringReason.PERFORMANCE.value == "performance"
        assert FiringReason.BUDGET.value == "budget"
        assert FiringReason.PROJECT_COMPLETION.value == "project_completion"

    def test_completeness(self) -> None:
        assert len(FiringReason) == 4


@pytest.mark.unit
class TestOnboardingStep:
    """OnboardingStep enum values."""

    def test_values(self) -> None:
        assert OnboardingStep.COMPANY_CONTEXT.value == "company_context"
        assert OnboardingStep.PROJECT_BRIEFING.value == "project_briefing"
        assert OnboardingStep.TEAM_INTRODUCTIONS.value == "team_introductions"

    def test_completeness(self) -> None:
        assert len(OnboardingStep) == 3


@pytest.mark.unit
class TestLifecycleEventType:
    """LifecycleEventType enum values."""

    def test_values(self) -> None:
        assert LifecycleEventType.HIRED.value == "hired"
        assert LifecycleEventType.ONBOARDED.value == "onboarded"
        assert LifecycleEventType.FIRED.value == "fired"
        assert LifecycleEventType.OFFBOARDED.value == "offboarded"
        assert LifecycleEventType.STATUS_CHANGED.value == "status_changed"
        assert LifecycleEventType.PROMOTED.value == "promoted"
        assert LifecycleEventType.DEMOTED.value == "demoted"

    def test_completeness(self) -> None:
        assert len(LifecycleEventType) == 7


@pytest.mark.unit
class TestPromotionDirection:
    """PromotionDirection enum values."""

    def test_values(self) -> None:
        assert PromotionDirection.PROMOTION.value == "promotion"
        assert PromotionDirection.DEMOTION.value == "demotion"

    def test_completeness(self) -> None:
        assert len(PromotionDirection) == 2


@pytest.mark.unit
class TestTrendDirection:
    """TrendDirection enum values."""

    def test_values(self) -> None:
        assert TrendDirection.IMPROVING.value == "improving"
        assert TrendDirection.STABLE.value == "stable"
        assert TrendDirection.DECLINING.value == "declining"
        assert TrendDirection.INSUFFICIENT_DATA.value == "insufficient_data"

    def test_completeness(self) -> None:
        assert len(TrendDirection) == 4
