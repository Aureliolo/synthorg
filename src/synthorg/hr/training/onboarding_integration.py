"""Training onboarding integration bridge.

Wires the TrainingService into the OnboardingService by building
a TrainingPlan from the new agent's identity and executing it.
"""

from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import OnboardingStep
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.training.models import ContentType, TrainingPlan, TrainingResult
from synthorg.hr.training.service import TrainingService
from synthorg.observability import get_logger
from synthorg.observability.events.training import (
    HR_TRAINING_AGENT_NOT_FOUND,
    HR_TRAINING_PLAN_CREATED,
    HR_TRAINING_REVIEW_PENDING,
)

logger = get_logger(__name__)


class TrainingOnboardingBridge:
    """Bridge between training service and onboarding.

    Looks up the agent identity, builds a TrainingPlan, executes
    it, and marks the LEARNED_FROM_SENIORS step complete only when
    the pipeline produced a terminal result (no pending review).

    Args:
        registry: Agent registry service.
        training_service: Training service orchestrator.
        onboarding_service: Onboarding service for step completion.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        training_service: TrainingService,
        onboarding_service: OnboardingService,
    ) -> None:
        self._registry = registry
        self._training_service = training_service
        self._onboarding_service = onboarding_service

    async def run_training_step(
        self,
        agent_id: NotBlankStr,
        *,
        override_sources: tuple[NotBlankStr, ...] = (),
        custom_caps: dict[ContentType, int] | None = None,
        skip_training: bool = False,
    ) -> TrainingResult:
        """Execute training for a newly hired agent.

        Marks the ``LEARNED_FROM_SENIORS`` onboarding step complete
        only when the run produced a terminal result: either
        ``skip_training`` was requested or the training result does
        not flag a pending review gate.  When the review gate is
        pending the step is intentionally left incomplete so the
        approval flow can resume onboarding once reviewed.

        Args:
            agent_id: The new agent's ID.
            override_sources: Explicit source agent IDs.
            custom_caps: Per-content-type cap overrides.
            skip_training: Whether to skip training.

        Returns:
            Training result.

        Raises:
            ValueError: If the agent is not found.
        """
        identity = await self._registry.get(agent_id)
        if identity is None:
            logger.warning(
                HR_TRAINING_AGENT_NOT_FOUND,
                agent_id=str(agent_id),
                stage="onboarding_bridge",
            )
            msg = f"Agent {agent_id!r} not found in registry"
            raise ValueError(msg)

        volume_caps = tuple(custom_caps.items()) if custom_caps is not None else None
        # Preserve None for department so downstream selectors can
        # distinguish "unknown" from a literal department named "None".
        department = (
            str(identity.department) if identity.department is not None else None
        )

        plan_kwargs: dict[str, object | None] = {
            "new_agent_id": str(identity.id),
            "new_agent_role": str(identity.role),
            "new_agent_level": identity.level,
            "new_agent_department": department,
            "override_sources": override_sources,
            "skip_training": skip_training,
            "created_at": datetime.now(UTC),
        }
        if volume_caps is not None:
            plan_kwargs["volume_caps"] = volume_caps

        plan = TrainingPlan(**plan_kwargs)  # type: ignore[arg-type]

        logger.info(
            HR_TRAINING_PLAN_CREATED,
            plan_id=str(plan.id),
            agent_id=str(agent_id),
            role=str(identity.role),
        )

        result = await self._training_service.execute(plan)

        if result.review_pending:
            logger.info(
                HR_TRAINING_REVIEW_PENDING,
                plan_id=str(plan.id),
                agent_id=str(agent_id),
                pending_approvals=len(result.pending_approvals),
            )
            return result

        await self._onboarding_service.complete_step(
            str(agent_id),
            OnboardingStep.LEARNED_FROM_SENIORS,
            notes=str(result.id),
        )

        return result
