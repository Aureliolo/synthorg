"""Unit tests for training onboarding integration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.hr.enums import OnboardingStep
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.training.models import TrainingResult
from synthorg.hr.training.onboarding_integration import (
    TrainingOnboardingBridge,
)
from synthorg.hr.training.service import TrainingService


def _now() -> datetime:
    return datetime.now(UTC)


def _make_identity(
    *,
    agent_id: str = "new-1",
    role: str = "engineer",
    level: SeniorityLevel = SeniorityLevel.JUNIOR,
) -> MagicMock:
    identity = MagicMock()
    identity.id = agent_id
    identity.role = role
    identity.level = level
    identity.department = "engineering"
    return identity


def _make_empty_result(agent_id: str = "new-1") -> TrainingResult:
    now = _now()
    return TrainingResult(
        plan_id="plan-1",
        new_agent_id=agent_id,
        started_at=now,
        completed_at=now,
    )


@pytest.mark.unit
class TestTrainingOnboardingBridge:
    """TrainingOnboardingBridge tests."""

    async def test_runs_training_and_completes_step(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = _make_identity()

        training_service = AsyncMock(spec=TrainingService)
        training_service.execute.return_value = _make_empty_result()

        onboarding = AsyncMock(spec=OnboardingService)

        bridge = TrainingOnboardingBridge(
            registry=registry,
            training_service=training_service,
            onboarding_service=onboarding,
        )

        result = await bridge.run_training_step("new-1")

        training_service.execute.assert_awaited_once()
        onboarding.complete_step.assert_awaited_once_with(
            "new-1",
            OnboardingStep.LEARNED_FROM_SENIORS,
            notes=result.id,
        )

    async def test_skip_training(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = _make_identity()

        training_service = AsyncMock(spec=TrainingService)
        training_service.execute.return_value = _make_empty_result()

        onboarding = AsyncMock(spec=OnboardingService)

        bridge = TrainingOnboardingBridge(
            registry=registry,
            training_service=training_service,
            onboarding_service=onboarding,
        )

        await bridge.run_training_step("new-1", skip_training=True)
        # Training service still runs (it short-circuits internally on skip)
        training_service.execute.assert_awaited_once()
        # Onboarding step should still complete since no review is pending.
        onboarding.complete_step.assert_awaited_once()

    async def test_passes_override_sources(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = _make_identity()

        training_service = AsyncMock(spec=TrainingService)
        training_service.execute.return_value = _make_empty_result()

        onboarding = AsyncMock(spec=OnboardingService)

        bridge = TrainingOnboardingBridge(
            registry=registry,
            training_service=training_service,
            onboarding_service=onboarding,
        )

        await bridge.run_training_step(
            "new-1",
            override_sources=("senior-a", "senior-b"),
        )

        call_args = training_service.execute.call_args
        plan = call_args[0][0]
        assert plan.override_sources == ("senior-a", "senior-b")

    async def test_raises_when_agent_not_found(self) -> None:
        registry = AsyncMock(spec=AgentRegistryService)
        registry.get.return_value = None

        bridge = TrainingOnboardingBridge(
            registry=registry,
            training_service=AsyncMock(spec=TrainingService),
            onboarding_service=AsyncMock(spec=OnboardingService),
        )

        with pytest.raises(ValueError, match="not found"):
            await bridge.run_training_step("nonexistent")
