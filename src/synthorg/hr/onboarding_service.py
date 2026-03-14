"""Onboarding service.

Manages agent onboarding checklists, step tracking, and
automatic activation upon checklist completion.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.enums import AgentStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import OnboardingStep
from synthorg.hr.errors import OnboardingError
from synthorg.hr.models import OnboardingChecklist, OnboardingStepRecord
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_ONBOARDING_COMPLETE,
    HR_ONBOARDING_STARTED,
    HR_ONBOARDING_STEP_COMPLETE,
)

if TYPE_CHECKING:
    from synthorg.hr.registry import AgentRegistryService

logger = get_logger(__name__)


class OnboardingService:
    """Manages onboarding checklists and step tracking.

    Creates checklists with all ``OnboardingStep`` values when
    onboarding starts. When all steps are completed, automatically
    transitions the agent to ACTIVE status via the registry.

    Args:
        registry: Agent registry for status updates.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
    ) -> None:
        self._registry = registry
        self._checklists: dict[str, OnboardingChecklist] = {}

    async def start_onboarding(self, agent_id: str) -> OnboardingChecklist:
        """Start onboarding for a newly hired agent.

        Creates a checklist with all onboarding steps in PENDING state.

        Args:
            agent_id: Agent to onboard.

        Returns:
            The created onboarding checklist.

        Raises:
            OnboardingError: If a checklist already exists.
        """
        agent = await self._registry.get(NotBlankStr(agent_id))
        if agent is None:
            msg = f"Agent {agent_id!r} not found in registry"
            logger.warning(HR_ONBOARDING_STARTED, agent_id=agent_id, error=msg)
            raise OnboardingError(msg)

        if agent_id in self._checklists:
            msg = f"Onboarding checklist already exists for agent {agent_id!r}"
            logger.warning(HR_ONBOARDING_STARTED, agent_id=agent_id, error=msg)
            raise OnboardingError(msg)

        steps = tuple(OnboardingStepRecord(step=step) for step in OnboardingStep)
        checklist = OnboardingChecklist(
            agent_id=agent_id,
            steps=steps,
            started_at=datetime.now(UTC),
        )
        self._checklists[agent_id] = checklist

        logger.info(
            HR_ONBOARDING_STARTED,
            agent_id=agent_id,
            step_count=len(steps),
        )
        return checklist

    async def complete_step(
        self,
        agent_id: str,
        step: OnboardingStep,
        *,
        notes: str = "",
    ) -> OnboardingChecklist:
        """Mark an onboarding step as complete.

        When all steps are completed, automatically transitions the
        agent to ACTIVE status.

        Args:
            agent_id: Agent being onboarded.
            step: The step to complete.
            notes: Optional notes for the step.

        Returns:
            Updated onboarding checklist.

        Raises:
            OnboardingError: If no checklist exists for the agent.
        """
        checklist = self._checklists.get(agent_id)
        if checklist is None:
            msg = f"No onboarding checklist for agent {agent_id!r}"
            logger.warning(
                HR_ONBOARDING_STEP_COMPLETE,
                agent_id=agent_id,
                error=msg,
            )
            raise OnboardingError(msg)

        now = datetime.now(UTC)
        step_found = any(s.step == step and not s.completed for s in checklist.steps)
        if not step_found:
            logger.warning(
                HR_ONBOARDING_STEP_COMPLETE,
                agent_id=agent_id,
                step=step.value,
                skipped="step_not_found_or_already_completed",
            )
            return checklist

        updated_steps = tuple(
            s.model_copy(
                update={
                    "completed": True,
                    "completed_at": now,
                    "notes": notes,
                },
            )
            if s.step == step and not s.completed
            else s
            for s in checklist.steps
        )

        updated = checklist.model_copy(update={"steps": updated_steps})

        # Check if all steps are now complete.
        if updated.is_complete and not checklist.is_complete:
            updated = updated.model_copy(update={"completed_at": now})
            await self._registry.update_status(agent_id, AgentStatus.ACTIVE)
            logger.info(HR_ONBOARDING_COMPLETE, agent_id=agent_id)

        self._checklists[agent_id] = updated

        logger.info(
            HR_ONBOARDING_STEP_COMPLETE,
            agent_id=agent_id,
            step=step.value,
        )
        return updated

    async def get_checklist(
        self,
        agent_id: str,
    ) -> OnboardingChecklist | None:
        """Retrieve the onboarding checklist for an agent.

        Args:
            agent_id: Agent to look up.

        Returns:
            The checklist, or None if not found.
        """
        return self._checklists.get(agent_id)
