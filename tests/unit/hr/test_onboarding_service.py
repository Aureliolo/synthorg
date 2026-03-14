"""Tests for OnboardingService."""

import pytest

from synthorg.core.enums import AgentStatus
from synthorg.hr.enums import OnboardingStep
from synthorg.hr.errors import OnboardingError
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from tests.unit.hr.conftest import make_agent_identity


@pytest.mark.unit
class TestOnboardingServiceStartOnboarding:
    """OnboardingService.start_onboarding tests."""

    async def test_start_creates_checklist(
        self,
        registry: AgentRegistryService,
        onboarding_service: OnboardingService,
    ) -> None:
        identity = make_agent_identity(
            name="onboard-target",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(identity)
        agent_id = str(identity.id)

        checklist = await onboarding_service.start_onboarding(agent_id)
        assert checklist.agent_id == agent_id
        assert len(checklist.steps) == len(OnboardingStep)
        assert checklist.is_complete is False
        assert checklist.completed_at is None

    async def test_start_all_steps_incomplete(
        self,
        registry: AgentRegistryService,
        onboarding_service: OnboardingService,
    ) -> None:
        identity = make_agent_identity(
            name="onboard-incomplete",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(identity)
        agent_id = str(identity.id)

        checklist = await onboarding_service.start_onboarding(agent_id)
        for step_rec in checklist.steps:
            assert step_rec.completed is False
            assert step_rec.completed_at is None

    async def test_start_duplicate_raises(
        self,
        registry: AgentRegistryService,
        onboarding_service: OnboardingService,
    ) -> None:
        identity = make_agent_identity(
            name="onboard-dup",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(identity)
        agent_id = str(identity.id)

        await onboarding_service.start_onboarding(agent_id)
        with pytest.raises(OnboardingError, match="already exists"):
            await onboarding_service.start_onboarding(agent_id)


@pytest.mark.unit
class TestOnboardingServiceCompleteStep:
    """OnboardingService.complete_step tests."""

    async def test_complete_single_step(
        self,
        registry: AgentRegistryService,
        onboarding_service: OnboardingService,
    ) -> None:
        identity = make_agent_identity(
            name="step-agent",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(identity)
        agent_id = str(identity.id)

        await onboarding_service.start_onboarding(agent_id)
        updated = await onboarding_service.complete_step(
            agent_id,
            OnboardingStep.COMPANY_CONTEXT,
            notes="Context loaded",
        )
        context_step = next(
            s for s in updated.steps if s.step == OnboardingStep.COMPANY_CONTEXT
        )
        assert context_step.completed is True
        assert context_step.completed_at is not None
        assert context_step.notes == "Context loaded"
        assert updated.is_complete is False

    async def test_complete_all_steps_activates_agent(
        self,
        registry: AgentRegistryService,
        onboarding_service: OnboardingService,
    ) -> None:
        # Register agent with ONBOARDING status.
        identity = make_agent_identity(
            name="new-hire",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(identity)
        agent_id = str(identity.id)

        await onboarding_service.start_onboarding(agent_id)

        for step in OnboardingStep:
            await onboarding_service.complete_step(agent_id, step)

        checklist = await onboarding_service.get_checklist(agent_id)
        assert checklist is not None
        assert checklist.is_complete is True
        assert checklist.completed_at is not None

        # Agent should now be ACTIVE.
        agent = await registry.get(agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.ACTIVE

    async def test_complete_step_no_checklist_raises(
        self,
        onboarding_service: OnboardingService,
    ) -> None:
        with pytest.raises(OnboardingError, match="No onboarding checklist"):
            await onboarding_service.complete_step(
                "nonexistent",
                OnboardingStep.COMPANY_CONTEXT,
            )


@pytest.mark.unit
class TestOnboardingServiceGetChecklist:
    """OnboardingService.get_checklist tests."""

    async def test_get_existing_checklist(
        self,
        registry: AgentRegistryService,
        onboarding_service: OnboardingService,
    ) -> None:
        identity = make_agent_identity(
            name="get-checklist-agent",
            status=AgentStatus.ONBOARDING,
        )
        await registry.register(identity)
        agent_id = str(identity.id)

        await onboarding_service.start_onboarding(agent_id)
        checklist = await onboarding_service.get_checklist(agent_id)
        assert checklist is not None
        assert checklist.agent_id == agent_id

    async def test_get_nonexistent_returns_none(
        self,
        onboarding_service: OnboardingService,
    ) -> None:
        result = await onboarding_service.get_checklist("nonexistent")
        assert result is None
