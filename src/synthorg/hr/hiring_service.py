"""Hiring service.

Orchestrates the hiring pipeline: request creation, candidate
generation, approval submission, and agent instantiation.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.approval import ApprovalItem
from synthorg.core.role import Skill
from synthorg.core.types import NotBlankStr, stable_agent_id
from synthorg.hr.enums import AgentStatus, HiringRequestStatus
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    HiringApprovalRequiredError,
    HiringError,
    HiringRejectedError,
    InvalidCandidateError,
    OnboardingError,
)
from synthorg.hr.models import CandidateCard, HiringRequest
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HIRING_REQUEST_STATUS_TRANSITIONED,
    HR_HIRING_APPROVAL_SUBMITTED,
    HR_HIRING_CANDIDATE_GENERATED,
    HR_HIRING_CANDIDATE_NOT_FOUND,
    HR_HIRING_INSTANTIATED,
    HR_HIRING_INSTANTIATION_FAILED,
    HR_HIRING_REQUEST_CREATED,
    HR_HIRING_REQUEST_INVALID,
    HR_HIRING_REQUEST_NOT_FOUND,
)
from synthorg.security.autonomy.enums import ActionType

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
        default_model_config: Optional default model configuration
            for newly created agents. Falls back to generic defaults
            if not provided.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        approval_store: ApprovalStoreProtocol | None = None,
        onboarding_service: OnboardingService | None = None,
        default_model_config: ModelConfig | None = None,
    ) -> None:
        self._registry = registry
        self._approval_store = approval_store
        self._onboarding_service = onboarding_service
        self._default_model_config = default_model_config
        self._requests: dict[str, HiringRequest] = {}
        # Serialises read-modify-write on ``_requests`` per request ID so two
        # concurrent pipeline steps on the same request cannot lose an update
        # or double-instantiate, while steps on different requests still run
        # concurrently (the lock is held across ``await`` points such as
        # approval-store writes and registry registration). HiringService has
        # no start/stop lifecycle, so each lock binds to the active loop on
        # first use without restart risk.
        self._requests_locks: dict[str, asyncio.Lock] = {}

    def _get_request(self, request_id: str) -> HiringRequest:
        """Look up a hiring request by ID.

        Args:
            request_id: The request ID to look up.

        Returns:
            The current hiring request.

        Raises:
            HiringError: If the request is not found.
        """
        request = self._requests.get(request_id)
        if request is None:
            msg = f"Hiring request {request_id!r} not found"
            logger.warning(
                HR_HIRING_REQUEST_NOT_FOUND,
                request_id=request_id,
                error=msg,
            )
            raise HiringError(msg)
        return request

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

        Raises:
            HiringError: If the related operation fails.
        """
        request = HiringRequest(
            requested_by=requested_by,
            department=department,
            role=role,
            level=self._parse_level(level),
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

    def _parse_level(self, level: str) -> SeniorityLevel:
        """Parse a seniority-level string into the enum.

        Args:
            level: Raw seniority level value.

        Returns:
            The parsed ``SeniorityLevel``.

        Raises:
            HiringError: If *level* is not a valid seniority level.
        """
        try:
            return SeniorityLevel(level)
        except ValueError as exc:
            msg = f"Invalid seniority level {level!r} for hiring request"
            logger.warning(HR_HIRING_REQUEST_INVALID, error=msg, level=level)
            raise HiringError(msg) from exc

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
        async with self._requests_locks.setdefault(str(request.id), asyncio.Lock()):
            request = self._get_request(str(request.id))
            candidate = self._build_candidate(request)
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

    def _build_candidate(self, request: HiringRequest) -> CandidateCard:
        """Build a candidate card from a hiring request's role/level defaults.

        Args:
            request: The hiring request to generate a candidate for.

        Returns:
            A new ``CandidateCard``.
        """
        return CandidateCard(
            name=NotBlankStr(f"{request.role}-{request.department}-agent"),
            role=request.role,
            department=request.department,
            level=request.level,
            skills=tuple(Skill(id=s, name=s) for s in request.required_skills),
            rationale=NotBlankStr(
                f"Generated for: {request.reason}",
            ),
            estimated_monthly_cost=(
                request.budget_limit_monthly
                if request.budget_limit_monthly is not None
                else 50.0
            ),
            template_source=request.template_name,
        )

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
        async with self._requests_locks.setdefault(str(request.id), asyncio.Lock()):
            request = self._get_request(str(request.id))

            candidate = next(
                (c for c in request.candidates if str(c.id) == candidate_id),
                None,
            )
            if candidate is None:
                msg = f"Candidate {candidate_id!r} not found on request {request.id!r}"
                logger.warning(
                    HR_HIRING_CANDIDATE_NOT_FOUND,
                    request_id=str(request.id),
                    error=msg,
                )
                raise InvalidCandidateError(msg)

            previous_status = request.status
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
                updated = await self._submit_approval_item(
                    request, candidate, candidate_id
                )

            self._requests[str(updated.id)] = updated

        # Emit the status-transition log only when the status actually
        # flipped: the auto-approve branch goes PENDING -> APPROVED,
        # but the manual-approval branch keeps the request at
        # ``previous_status`` (the approval-store flow only stamps a
        # selected candidate / approval id).  Logging in the
        # no-transition case would lie about state.
        if updated.status != previous_status:
            logger.info(
                HIRING_REQUEST_STATUS_TRANSITIONED,
                request_id=str(updated.id),
                from_status=previous_status.value,
                to_status=updated.status.value,
            )

        logger.info(
            HR_HIRING_APPROVAL_SUBMITTED,
            request_id=str(request.id),
            candidate_id=candidate_id,
            auto_approved=self._approval_store is None,
        )
        return updated

    async def _submit_approval_item(
        self,
        request: HiringRequest,
        candidate: CandidateCard,
        candidate_id: str,
    ) -> HiringRequest:
        """Create and store an approval item for a candidate.

        Args:
            request: The hiring request.
            candidate: The candidate to approve.
            candidate_id: ID of the candidate.

        Returns:
            Updated request with approval metadata.
        """
        assert self._approval_store is not None  # noqa: S101
        approval_id = str(uuid4())
        approval_item = ApprovalItem(
            id=UUID(approval_id),
            action_type=NotBlankStr(ActionType.ORG_HIRE),
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
        return request.model_copy(
            update={
                "selected_candidate_id": candidate_id,
                "approval_id": approval_id,
            },
        )

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
        async with self._requests_locks.setdefault(str(request.id), asyncio.Lock()):
            request = self._get_request(str(request.id))
            self._validate_instantiation_status(request)
            candidate = self._find_selected_candidate(request)

            identity = self._build_agent_identity(candidate)
            await self._register_agent(identity, request)
            self._apply_instantiated_status(request)

        # Onboarding runs outside the lock: it is non-fatal and should
        # not hold up other pipeline steps on the same request.
        await self._try_onboard(identity)

        logger.info(
            HR_HIRING_INSTANTIATED,
            request_id=str(request.id),
            agent_id=str(identity.id),
            agent_name=str(identity.name),
        )
        # INSTANTIATED is terminal: no further operation targets this request,
        # so drop its in-memory model and lock instead of retaining both (the
        # model can carry large candidate payloads) for the process lifetime.
        self._evict_request(str(request.id))
        return identity

    def _evict_request(self, request_id: str) -> None:
        """Drop a terminal request and its per-request lock.

        Keeps ``_requests`` and ``_requests_locks`` bounded under continuous
        hiring activity; completed requests are read back through the
        persistence layer, not this in-memory cache.

        Args:
            request_id: The terminal request to forget.
        """
        self._requests.pop(request_id, None)
        self._requests_locks.pop(request_id, None)

    def _apply_instantiated_status(self, request: HiringRequest) -> None:
        """Persist the APPROVED -> INSTANTIATED status flip and log it.

        Logs AFTER the dict write succeeds and before downstream
        callbacks; ``_validate_instantiation_status`` enforces the
        ``previous_status == APPROVED`` invariant, so this always emits an
        APPROVED -> INSTANTIATED transition.

        Args:
            request: The approved request being instantiated.
        """
        previous_status = request.status
        updated = request.model_copy(
            update={"status": HiringRequestStatus.INSTANTIATED},
        )
        self._requests[str(updated.id)] = updated
        logger.info(
            HIRING_REQUEST_STATUS_TRANSITIONED,
            request_id=str(updated.id),
            from_status=previous_status.value,
            to_status=updated.status.value,
        )

    def _validate_instantiation_status(self, request: HiringRequest) -> None:
        """Validate that the request is in a valid state for instantiation.

        Args:
            request: The hiring request to validate.

        Raises:
            HiringError: If already instantiated.
            HiringRejectedError: If request was rejected.
            HiringApprovalRequiredError: If request needs approval.
            InvalidCandidateError: If no candidate selected.
        """
        if request.status == HiringRequestStatus.INSTANTIATED:
            msg = f"Hiring request {request.id!r} is already instantiated"
            logger.warning(
                HR_HIRING_INSTANTIATION_FAILED,
                request_id=str(request.id),
                error=msg,
            )
            raise HiringError(msg)
        if request.status == HiringRequestStatus.REJECTED:
            msg = f"Hiring request {request.id!r} was rejected"
            logger.warning(
                HR_HIRING_INSTANTIATION_FAILED,
                request_id=str(request.id),
                error=msg,
            )
            raise HiringRejectedError(msg)
        if request.status == HiringRequestStatus.PENDING:
            msg = f"Hiring request {request.id!r} requires approval"
            logger.warning(
                HR_HIRING_INSTANTIATION_FAILED,
                request_id=str(request.id),
                error=msg,
            )
            raise HiringApprovalRequiredError(msg)
        if request.selected_candidate_id is None:
            msg = f"No candidate selected on request {request.id!r}"
            logger.warning(
                HR_HIRING_INSTANTIATION_FAILED,
                request_id=str(request.id),
                error=msg,
            )
            raise InvalidCandidateError(msg)

    def _find_selected_candidate(self, request: HiringRequest) -> CandidateCard:
        """Find the selected candidate on a hiring request.

        Args:
            request: The hiring request.

        Returns:
            The selected candidate card.

        Raises:
            InvalidCandidateError: If the selected candidate is not found.
        """
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
            logger.warning(
                HR_HIRING_INSTANTIATION_FAILED,
                request_id=str(request.id),
                error=msg,
            )
            raise InvalidCandidateError(msg)
        return candidate

    def _build_agent_identity(self, candidate: CandidateCard) -> AgentIdentity:
        """Build an AgentIdentity from a candidate card.

        Args:
            candidate: The candidate to convert.

        Returns:
            A new agent identity.

        Raises:
            HiringError: If the identity cannot be constructed.
        """
        model = self._default_model_config or ModelConfig(
            provider=NotBlankStr("default-provider"),
            model_id=NotBlankStr("default-model-001"),
        )
        status = (
            AgentStatus.ONBOARDING
            if self._onboarding_service is not None
            else AgentStatus.ACTIVE
        )
        try:
            return AgentIdentity(
                id=stable_agent_id(candidate.name),
                name=candidate.name,
                role=candidate.role,
                department=candidate.department,
                level=candidate.level,
                skills=SkillSet(primary=candidate.skills),
                model=model,
                status=status,
                hiring_date=datetime.now(UTC).date(),
            )
        except (ValidationError, ValueError) as exc:
            msg = f"Failed to construct AgentIdentity for candidate {candidate.id!r}"
            logger.warning(
                HR_HIRING_INSTANTIATION_FAILED,
                candidate_id=str(candidate.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise HiringError(msg) from exc

    async def _register_agent(
        self,
        identity: AgentIdentity,
        request: HiringRequest,
    ) -> None:
        """Register a new agent identity in the registry.

        Args:
            identity: The agent identity to register.
            request: The associated hiring request (for error context).

        Raises:
            HiringError: If registration fails.
        """
        try:
            await self._registry.register(identity)
        except AgentAlreadyRegisteredError as exc:
            msg = f"Agent already registered for request {request.id!r}"
            logger.warning(
                HR_HIRING_INSTANTIATION_FAILED,
                request_id=str(request.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise HiringError(msg) from exc

    async def _try_onboard(self, identity: AgentIdentity) -> None:
        """Attempt onboarding if the service is available.

        Onboarding failure is non-fatal: the agent is already
        registered and can be onboarded later.

        Args:
            identity: The newly created agent identity.
        """
        if self._onboarding_service is None:
            return
        try:
            await self._onboarding_service.start_onboarding(str(identity.id))
        except OnboardingError as exc:
            logger.warning(
                HR_HIRING_INSTANTIATED,
                agent_id=str(identity.id),
                warning="onboarding_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
