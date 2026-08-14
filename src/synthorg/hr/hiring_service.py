# module-kind: service
"""Hiring service.

Orchestrates the hiring pipeline: request creation, candidate
generation, approval submission, and agent instantiation.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.persistence_errors import PersistenceError
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus, HiringRequestStatus
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    HiringError,
    InvalidCandidateError,
    OnboardingError,
)
from synthorg.hr.hiring_candidates import (
    build_agent_identity,
    build_candidate,
    build_hire_approval_item,
    select_candidate,
)
from synthorg.hr.hiring_transitions import validate_decidable, validate_instantiable
from synthorg.hr.models import CandidateCard, HiringRequest
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HIRING_REQUEST_STATUS_TRANSITIONED,
    HR_HIRING_APPROVAL_SUBMITTED,
    HR_HIRING_APPROVED,
    HR_HIRING_CANDIDATE_GENERATED,
    HR_HIRING_CANDIDATE_NOT_FOUND,
    HR_HIRING_INSTANTIATED,
    HR_HIRING_INSTANTIATION_FAILED,
    HR_HIRING_MODEL_UNSET,
    HR_HIRING_PERSIST_FAILED,
    HR_HIRING_REJECTED,
    HR_HIRING_REQUEST_CREATED,
    HR_HIRING_REQUEST_NOT_FOUND,
    HR_HIRING_REQUESTS_HYDRATED,
)
from synthorg.persistence.hiring_request_protocol import (
    HiringRequestRepository,
)
from synthorg.settings.bound_model import resolve_bound_model_live
from synthorg.settings.kill_switch import require_configured_model
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

_PERSIST_TIMEOUT_SECONDS: Final[float] = 5.0
_HYDRATE_PAGE_SIZE: Final[int] = 100

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
        config_resolver: Settings resolver, read per instantiation for the
            pair a new hire is bound to. Without one (or with the setting
            unset) instantiation refuses rather than inventing a pair.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        approval_store: ApprovalStoreProtocol | None = None,
        onboarding_service: OnboardingService | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
        request_repo: HiringRequestRepository | None = None,
    ) -> None:
        self._registry = registry
        self._approval_store = approval_store
        self._onboarding_service = onboarding_service
        self._config_resolver = config_resolver
        # Durable backing store for in-flight requests. When attached
        # (production) every lifecycle write is best-effort persisted and
        # the in-flight set is rehydrated at startup so an approved
        # request is not orphaned by a restart between approval and
        # instantiation.
        self._request_repo = request_repo
        self._requests: dict[str, HiringRequest] = {}
        # Serialises read-modify-write on ``_requests`` per request ID so two
        # concurrent pipeline steps on the same request cannot lose an update
        # or double-instantiate, while steps on different requests still run
        # concurrently (the lock is held across ``await`` points such as
        # approval-store writes and registry registration). The map evicts a
        # request's lock once no step holds it, so it stays bounded.
        self._request_locks: RefcountedLockMap[str] = RefcountedLockMap()

    def attach_persistence(self, *, request_repo: HiringRequestRepository) -> None:
        """Attach the durable request repo after boot.

        The service is built in the construction phase before
        persistence exists; this is called from the on-startup wiring
        hook. Pair with :meth:`hydrate`.
        """
        self._request_repo = request_repo

    async def hydrate(self) -> None:
        """Load durable in-flight requests into the in-memory set.

        Idempotent and a no-op when no repository is attached.
        """
        if self._request_repo is None:
            return
        loaded: dict[str, HiringRequest] = {}
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded startup pagination
        while True:
            # Bound each page read so a hung backend cannot stall the on-startup
            # wiring hook indefinitely; mirrors the write-path timeout in
            # ``_store``. A timeout surfaces to the caller (wire_scaling) where
            # it degrades to leaving the service unwired rather than hanging.
            async with asyncio.timeout(_PERSIST_TIMEOUT_SECONDS):
                batch = await self._request_repo.list_items(
                    limit=_HYDRATE_PAGE_SIZE, offset=offset
                )
            for request in batch:
                loaded[str(request.id)] = request
            if len(batch) < _HYDRATE_PAGE_SIZE:
                break
            offset += _HYDRATE_PAGE_SIZE
        self._requests = loaded
        logger.info(HR_HIRING_REQUESTS_HYDRATED, requests=len(loaded))

    async def _store(
        self,
        request: HiringRequest,
        *,
        require_persist: bool = False,
    ) -> None:
        """Update the in-memory set and persist the request.

        With ``require_persist`` a persistence failure raises ``HiringError``
        instead of being swallowed, so a caller that already performed an
        external side effect (approval-item write, agent registration) cannot
        leave the request transition durable-less: a restart would otherwise
        rehydrate stale request state while the side effect already exists,
        wedging retries. A persistence-less boot (no repo) stays in-memory
        only and never raises, since nothing is rehydrated on restart.

        Raises:
            HiringError: If ``require_persist`` and the durable save fails.
        """
        self._requests[str(request.id)] = request
        if self._request_repo is None:
            return
        try:
            async with asyncio.timeout(_PERSIST_TIMEOUT_SECONDS):
                await self._request_repo.save(request)
        except (PersistenceError, TimeoutError) as exc:
            logger.warning(
                HR_HIRING_PERSIST_FAILED,
                request_id=str(request.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if require_persist:
                msg = f"Failed to persist hiring request {request.id!s}"
                raise HiringError(msg) from exc

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

    async def create_request(
        self,
        *,
        requested_by: NotBlankStr,
        department: NotBlankStr,
        role: NotBlankStr,
        required_skills: tuple[NotBlankStr, ...] = (),
        reason: NotBlankStr,
        agent_delegate: NotBlankStr | None = None,
        budget_limit_monthly: float | None = None,
        template_name: str | None = None,
    ) -> HiringRequest:
        """Create a new hiring request.

        Args:
            requested_by: Request initiator.
            department: Target department.
            role: Desired role.
            required_skills: Required skills.
            reason: Business justification.
            agent_delegate: Existing agent assigned to absorb queued work
                while this hire instantiates (overflow handler).
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
            required_skills=required_skills,
            reason=reason,
            agent_delegate=agent_delegate,
            budget_limit_monthly=budget_limit_monthly,
            template_name=template_name,
            created_at=datetime.now(UTC),
        )
        await self._store(request)

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

        Builds a ``CandidateCard`` from role defaults. In the
        future, this can be extended with template presets and LLM
        customization.

        Args:
            request: The hiring request to generate a candidate for.

        Returns:
            Updated request with the new candidate appended.
        """
        async with self._request_locks.acquire(str(request.id)):
            request = self._get_request(str(request.id))
            candidate = build_candidate(request)
            updated = request.model_copy(
                update={"candidates": (*request.candidates, candidate)},
            )
            await self._store(updated)

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
        async with self._request_locks.acquire(str(request.id)):
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
                # Auto-approve when no approval store: no external side effect,
                # so a swallowed persist failure only loses an in-memory status
                # flip a restart would discard cleanly.
                updated = request.model_copy(
                    update={
                        "status": HiringRequestStatus.APPROVED,
                        "selected_candidate_id": candidate_id,
                    },
                )
                await self._store(updated)
            else:
                # Create an approval item (durable external side effect), then
                # require the request transition to persist: a swallowed save
                # would let a restart rehydrate the pre-approval request while
                # the approval item already exists, wedging retries.
                updated = await self._submit_approval_item(
                    request, candidate, candidate_id
                )
                await self._store(updated, require_persist=True)

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
        await self._approval_store.add(
            build_hire_approval_item(
                request,
                candidate,
                candidate_id=candidate_id,
                approval_id=approval_id,
            )
        )
        return request.model_copy(
            update={
                "selected_candidate_id": candidate_id,
                "approval_id": approval_id,
            },
        )

    def find_by_approval_id(self, approval_id: str) -> HiringRequest | None:
        """Find the in-flight request an approval item decides.

        The in-flight set is the authority: it is rehydrated from the
        repository at startup, so a decision that arrives after a restart
        still finds its request.

        Args:
            approval_id: The decided approval item's id.

        Returns:
            The request carrying that approval, or ``None`` when none does
            (every non-hiring approval lands here, and must read as a miss
            rather than an error).
        """
        return next(
            (r for r in self._requests.values() if r.approval_id == approval_id),
            None,
        )

    def find_open_request_for_role(self, role: str) -> HiringRequest | None:
        """Find an undecided request already open for *role*.

        Args:
            role: The role name being staffed.

        Returns:
            The open request, or ``None`` when none is in flight. Callers use
            this to keep one open request per unstaffed role rather than
            opening a fresh one on every pass.
        """
        return next(
            (
                r
                for r in self._requests.values()
                if r.status is HiringRequestStatus.PENDING and str(r.role) == role
            ),
            None,
        )

    def find_approved_requests(self) -> tuple[HiringRequest, ...]:
        """Return every request a human approved that has not been hired yet.

        Approval and instantiation are separate steps, so a failure between
        them (an unbound new-hire pair, a registry outage) leaves an APPROVED
        request with no agent. The staffing sweep reads this to finish those
        rather than leaving the operator's decision half-applied.

        Returns:
            The approved-but-not-instantiated requests, oldest first so a
            sweep applies decisions in the order they were made.
        """
        return tuple(
            sorted(
                (
                    r
                    for r in self._requests.values()
                    if r.status is HiringRequestStatus.APPROVED
                ),
                key=lambda r: r.created_at,
            )
        )

    async def approve_request(
        self,
        request_id: str,
        *,
        decided_by: str,
    ) -> HiringRequest:
        """Record a human's approval of a hiring request.

        The step between ``submit_for_approval`` and ``instantiate_agent``:
        without it an approved hire flipped an approval row and registered
        nobody, so the whole tail from "a human said yes" to "the agent
        exists" was unreachable.

        Args:
            request_id: The request being approved.
            decided_by: Who approved, for the log.

        Returns:
            The approved request.

        Raises:
            HiringError: If the request is not awaiting a decision.
            InvalidCandidateError: If no candidate was selected, which means
                the request never reached the approval stage.
        """
        async with self._request_locks.acquire(request_id):
            request = self._get_request(request_id)
            validate_decidable(request, decision="approve")
            if request.selected_candidate_id is None:
                msg = (
                    f"Hiring request {request_id!r} carries no selected "
                    "candidate; it was never submitted for approval"
                )
                logger.warning(
                    HR_HIRING_INSTANTIATION_FAILED,
                    request_id=request_id,
                    error=msg,
                )
                raise InvalidCandidateError(msg)
            updated = request.model_copy(
                update={"status": HiringRequestStatus.APPROVED},
            )
            await self._store(updated, require_persist=True)

        logger.info(
            HIRING_REQUEST_STATUS_TRANSITIONED,
            request_id=request_id,
            from_status=request.status.value,
            to_status=updated.status.value,
        )
        logger.info(HR_HIRING_APPROVED, request_id=request_id, decided_by=decided_by)
        return updated

    async def reject_request(
        self,
        request_id: str,
        *,
        decided_by: str,
        reason: str | None = None,
    ) -> HiringRequest:
        """Record a human's rejection of a hiring request.

        Args:
            request_id: The request being rejected.
            decided_by: Who rejected, for the log.
            reason: Optional explanation, for the log.

        Returns:
            The rejected request.

        Raises:
            HiringError: If the request is not awaiting a decision.
        """
        async with self._request_locks.acquire(request_id):
            request = self._get_request(request_id)
            validate_decidable(request, decision="reject")
            updated = request.model_copy(
                update={"status": HiringRequestStatus.REJECTED},
            )
            await self._store(updated, require_persist=True)

        logger.info(
            HIRING_REQUEST_STATUS_TRANSITIONED,
            request_id=request_id,
            from_status=request.status.value,
            to_status=updated.status.value,
        )
        logger.info(
            HR_HIRING_REJECTED,
            request_id=request_id,
            decided_by=decided_by,
            has_reason=reason is not None,
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
            ServiceUnavailableError: If no pair is bound for new hires.
            HiringError: If instantiation fails.
        """
        async with self._request_locks.acquire(str(request.id)):
            request = self._get_request(str(request.id))
            validate_instantiable(request)
            candidate = select_candidate(request)

            identity = build_agent_identity(
                candidate,
                model=await self._resolve_new_hire_model(),
                status=(
                    AgentStatus.ONBOARDING
                    if self._onboarding_service is not None
                    else AgentStatus.ACTIVE
                ),
            )
            await self._register_agent(identity, request)
            await self._apply_instantiated_status(request)

        # Onboarding runs outside the lock: it is non-fatal and should
        # not hold up other pipeline steps on the same request.
        await self._try_onboard(identity)

        logger.info(
            HR_HIRING_INSTANTIATED,
            request_id=str(request.id),
            agent_id=str(identity.id),
            agent_name=str(identity.name),
        )
        # INSTANTIATED is the terminal state; the request stays in
        # ``_requests`` so a retried instantiate_agent() (or any queued
        # same-request step) reads it back and gets the precise
        # already-instantiated validation error. This service has no
        # persistence read path, so evicting here would turn a completed
        # request into a misleading "not found". The per-request lock still
        # self-evicts via ``RefcountedLockMap`` once no step holds it, which
        # is what keeps the unbounded-lock growth in check.
        return identity

    async def _apply_instantiated_status(self, request: HiringRequest) -> None:
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
        # The agent is already registered by the caller, so this terminal
        # transition must persist: a swallowed save would let a restart
        # rehydrate the APPROVED request and re-drive instantiation against
        # an agent that already exists.
        await self._store(updated, require_persist=True)
        logger.info(
            HIRING_REQUEST_STATUS_TRANSITIONED,
            request_id=str(updated.id),
            from_status=previous_status.value,
            to_status=updated.status.value,
        )

    async def _resolve_new_hire_model(self) -> ModelConfig:
        """Read the pair a new hire is bound to, refusing an unset one.

        Read live per instantiation rather than captured at wiring, so an
        operator who binds the pair after boot can approve a hire without a
        restart. There is deliberately nothing to fall back to: an agent
        registered against a placeholder provider joins the roster looking
        staffed and fails every dispatch it is ever given.

        Returns:
            The bound pair the new agent runs on.

        Raises:
            ServiceUnavailableError: When no pair is bound.
        """
        ref = require_configured_model(
            await resolve_bound_model_live(
                self._config_resolver,
                namespace="hr",
                key="new_hire_model",
                unset_event=HR_HIRING_MODEL_UNSET,
            ),
            namespace="hr",
            key="new_hire_model",
            feature_label="hiring",
        )
        return ModelConfig(
            provider=NotBlankStr(ref.provider),
            model_id=NotBlankStr(ref.model_id),
        )

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
