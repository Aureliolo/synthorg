"""Hiring service.

Orchestrates the hiring pipeline: request creation, candidate
generation, approval submission, and agent instantiation.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ai_company.core.agent import AgentIdentity, ModelConfig
from ai_company.core.approval import ApprovalItem
from ai_company.core.enums import (
    ActionType,
    AgentStatus,
    ApprovalRiskLevel,
    SeniorityLevel,
)
from ai_company.core.types import NotBlankStr
from ai_company.hr.enums import HiringRequestStatus
from ai_company.hr.errors import (
    HiringApprovalRequiredError,
    HiringError,
    HiringRejectedError,
    InvalidCandidateError,
)
from ai_company.hr.models import CandidateCard, HiringRequest
from ai_company.hr.registry import AgentRegistryService  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.hr import (
    HR_HIRING_APPROVAL_SUBMITTED,
    HR_HIRING_CANDIDATE_GENERATED,
    HR_HIRING_INSTANTIATED,
    HR_HIRING_REQUEST_CREATED,
)

if TYPE_CHECKING:
    from ai_company.api.approval_store import ApprovalStore
    from ai_company.hr.onboarding_service import OnboardingService

logger = get_logger(__name__)


class HiringService:
    """Orchestrates the hiring pipeline.

    Manages the lifecycle of hiring requests from creation through
    candidate generation, approval, and agent instantiation.

    Args:
        registry: Agent registry for registering new agents.
        approval_store: Optional approval store for human approval.
        onboarding_service: Optional onboarding service to start
            onboarding after instantiation.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        approval_store: ApprovalStore | None = None,
        onboarding_service: OnboardingService | None = None,
    ) -> None:
        self._registry = registry
        self._approval_store = approval_store
        self._onboarding_service = onboarding_service
        self._requests: dict[str, HiringRequest] = {}

    async def create_request(  # noqa: PLR0913
        self,
        *,
        requested_by: NotBlankStr,
        department: NotBlankStr,
        role: NotBlankStr,
        level: str,
        required_skills: tuple[NotBlankStr, ...] = (),
        reason: NotBlankStr,
        budget_limit_monthly: float | None = None,
        template_name: str | None = None,
    ) -> HiringRequest:
        """Create a new hiring request.

        Args:
            requested_by: Request initiator.
            department: Target department.
            role: Desired role.
            level: Desired seniority level.
            required_skills: Required skills.
            reason: Business justification.
            budget_limit_monthly: Optional monthly budget limit.
            template_name: Template for candidate generation.

        Returns:
            The created hiring request.
        """
        request = HiringRequest(
            requested_by=requested_by,
            department=department,
            role=role,
            level=SeniorityLevel(level),
            required_skills=required_skills,
            reason=reason,
            budget_limit_monthly=budget_limit_monthly,
            template_name=template_name,
            created_at=datetime.now(UTC),
        )
        self._requests[str(request.id)] = request

        logger.info(
            HR_HIRING_REQUEST_CREATED,
            request_id=str(request.id),
            department=str(department),
            role=str(role),
        )
        return request

    async def generate_candidate(
        self,
        request: HiringRequest,
    ) -> HiringRequest:
        """Generate a candidate card for a hiring request.

        Builds a ``CandidateCard`` from role/level defaults. In the
        future, this can be extended with template presets and LLM
        customization.

        Args:
            request: The hiring request to generate a candidate for.

        Returns:
            Updated request with the new candidate appended.
        """
        candidate = CandidateCard(
            name=NotBlankStr(f"{request.role}-{request.department}-agent"),
            role=request.role,
            department=request.department,
            level=request.level,
            skills=request.required_skills,
            rationale=NotBlankStr(
                f"Generated for: {request.reason}",
            ),
            estimated_monthly_cost=50.0,
            template_source=request.template_name,
        )

        updated = request.model_copy(
            update={"candidates": (*request.candidates, candidate)},
        )
        self._requests[str(updated.id)] = updated

        logger.info(
            HR_HIRING_CANDIDATE_GENERATED,
            request_id=str(request.id),
            candidate_id=str(candidate.id),
        )
        return updated

    async def submit_for_approval(
        self,
        request: HiringRequest,
        candidate_id: str,
    ) -> HiringRequest:
        """Submit a candidate for approval.

        If no approval store is configured, auto-approves the request.

        Args:
            request: The hiring request.
            candidate_id: ID of the candidate to approve.

        Returns:
            Updated request with approval status.

        Raises:
            InvalidCandidateError: If the candidate ID is not found.
        """
        # Validate candidate exists.
        candidate = next(
            (c for c in request.candidates if str(c.id) == candidate_id),
            None,
        )
        if candidate is None:
            msg = f"Candidate {candidate_id!r} not found on request {request.id!r}"
            raise InvalidCandidateError(msg)

        if self._approval_store is None:
            # Auto-approve when no approval store.
            updated = request.model_copy(
                update={
                    "status": HiringRequestStatus.APPROVED,
                    "selected_candidate_id": candidate_id,
                },
            )
        else:
            # Create an approval item.
            approval_id = str(uuid4())
            approval_item = ApprovalItem(
                id=NotBlankStr(approval_id),
                action_type=NotBlankStr(ActionType.HIRING),
                title=NotBlankStr(
                    f"Hire {candidate.name} as {candidate.role}",
                ),
                description=NotBlankStr(request.reason),
                requested_by=request.requested_by,
                risk_level=ApprovalRiskLevel.HIGH,
                created_at=datetime.now(UTC),
                metadata={
                    "request_id": str(request.id),
                    "candidate_id": candidate_id,
                },
            )
            await self._approval_store.add(approval_item)
            updated = request.model_copy(
                update={
                    "selected_candidate_id": candidate_id,
                    "approval_id": approval_id,
                },
            )

        self._requests[str(updated.id)] = updated

        logger.info(
            HR_HIRING_APPROVAL_SUBMITTED,
            request_id=str(request.id),
            candidate_id=candidate_id,
            auto_approved=self._approval_store is None,
        )
        return updated

    async def instantiate_agent(
        self,
        request: HiringRequest,
    ) -> AgentIdentity:
        """Instantiate an agent from an approved hiring request.

        Args:
            request: The approved hiring request.

        Returns:
            The newly created agent identity.

        Raises:
            HiringApprovalRequiredError: If request is not approved.
            HiringRejectedError: If request was rejected.
            InvalidCandidateError: If no candidate is selected.
            HiringError: If instantiation fails.
        """
        if request.status == HiringRequestStatus.REJECTED:
            msg = f"Hiring request {request.id!r} was rejected"
            raise HiringRejectedError(msg)
        if request.status == HiringRequestStatus.PENDING:
            msg = f"Hiring request {request.id!r} requires approval"
            raise HiringApprovalRequiredError(msg)
        if request.selected_candidate_id is None:
            msg = f"No candidate selected on request {request.id!r}"
            raise InvalidCandidateError(msg)

        candidate = next(
            (
                c
                for c in request.candidates
                if str(c.id) == request.selected_candidate_id
            ),
            None,
        )
        if candidate is None:
            msg = (
                f"Selected candidate {request.selected_candidate_id!r} "
                f"not found on request {request.id!r}"
            )
            raise InvalidCandidateError(msg)

        try:
            identity = AgentIdentity(
                name=candidate.name,
                role=candidate.role,
                department=candidate.department,
                level=candidate.level,
                model=ModelConfig(
                    provider="default-provider",
                    model_id="default-model-001",
                ),
                status=AgentStatus.ONBOARDING,
                hiring_date=datetime.now(UTC).date(),
            )
            await self._registry.register(identity)
        except Exception as exc:
            msg = f"Failed to instantiate agent for request {request.id!r}"
            logger.exception(
                HR_HIRING_INSTANTIATED,
                request_id=str(request.id),
                error=str(exc),
            )
            raise HiringError(msg) from exc

        # Update request status.
        updated = request.model_copy(
            update={"status": HiringRequestStatus.INSTANTIATED},
        )
        self._requests[str(updated.id)] = updated

        # Start onboarding if service is available.
        if self._onboarding_service is not None:
            await self._onboarding_service.start_onboarding(str(identity.id))

        logger.info(
            HR_HIRING_INSTANTIATED,
            request_id=str(request.id),
            agent_id=str(identity.id),
            agent_name=str(identity.name),
        )
        return identity
