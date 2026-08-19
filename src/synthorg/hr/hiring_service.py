# module-kind: service
"""Hiring service.

Orchestrates the hiring pipeline: request creation, candidate
generation, approval submission, and agent instantiation.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.role_catalog import role_is_gate_role
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus, HiringRequestStatus
from synthorg.hr.errors import (
    HiringAlreadyInFlightError,
    HiringError,
    InvalidCandidateError,
)
from synthorg.hr.hire_model_proposal import HireModelProposal, ProviderCatalogue
from synthorg.hr.hiring_approval_submission import (
    build_approval,
    propose_models,
    recommended_ref,
)
from synthorg.hr.hiring_candidates import (
    build_agent_identity,
    build_candidate,
    select_candidate,
)
from synthorg.hr.hiring_instantiation import (
    register_agent,
    resolve_hire_model,
    try_onboard,
)
from synthorg.hr.hiring_request_durability import read_all, save_request
from synthorg.hr.hiring_request_queries import (
    approved_not_instantiated,
    by_approval_id,
    in_flight_for_role,
)
from synthorg.hr.hiring_transitions import validate_decidable, validate_instantiable
from synthorg.hr.models import CandidateCard, HiringRequest
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HIRING_REQUEST_STATUS_TRANSITIONED,
    HR_HIRING_APPROVAL_SUBMITTED,
    HR_HIRING_APPROVED,
    HR_HIRING_CANDIDATE_GENERATED,
    HR_HIRING_CANDIDATE_NOT_FOUND,
    HR_HIRING_INSTANTIATED,
    HR_HIRING_INSTANTIATION_FAILED,
    HR_HIRING_REJECTED,
    HR_HIRING_REQUEST_CREATED,
    HR_HIRING_REQUEST_INVALID,
    HR_HIRING_REQUEST_NOT_FOUND,
    HR_HIRING_REQUESTS_HYDRATED,
)
from synthorg.persistence.hiring_request_protocol import (
    HiringRequestRepository,
)
from synthorg.settings.model_ref import parse_model_ref, serialize_model_ref
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

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
        config_resolver: Settings resolver, read when an approval is raised
            for the company's model-spend profile, which decides which
            proposed pair is recommended.
        provider_catalogue: The operator's configured providers, read live
            when a hire is proposed so the pairs offered are the ones they
            actually have. Without one nothing is proposable and the approval
            says so rather than offering a pair that does not exist.
    """

    def __init__(
        self,
        *,
        registry: AgentRegistryService,
        approval_store: ApprovalStoreProtocol | None = None,
        onboarding_service: OnboardingService | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
        request_repo: HiringRequestRepository | None = None,
        provider_catalogue: ProviderCatalogue | None = None,
    ) -> None:
        self._registry = registry
        self._approval_store = approval_store
        self._onboarding_service = onboarding_service
        self._config_resolver = config_resolver
        self._provider_catalogue = provider_catalogue
        # Durable backing store for in-flight requests. When attached
        # (production) every lifecycle write is best-effort persisted and
        # the in-flight set is rehydrated at startup so an approved
        # request is not orphaned by a restart between approval and
        # instantiation.
        self._request_repo = request_repo
        self._requests: dict[str, HiringRequest] = {}
        # False until a hydration pass has actually read the durable set.
        # Boot isolates a hydration failure so the pipeline comes up
        # degraded rather than not at all, and without this the degraded
        # state is permanent: an approved request written before the
        # restart stays invisible for the life of the process.
        self._hydrated: bool = False
        # Serialises read-modify-write on ``_requests`` per request ID so two
        # concurrent pipeline steps on the same request cannot lose an update
        # or double-instantiate, while steps on different requests still run
        # concurrently (the lock is held across ``await`` points such as
        # approval-store writes and registry registration). The map evicts a
        # request's lock once no step holds it, so it stays bounded.
        self._request_locks: RefcountedLockMap[str] = RefcountedLockMap()
        # Keyed by ROLE, not request: the single-in-flight-hire invariant is a
        # statement about a role, and the check that enforces it runs before
        # any request exists to key a lock on.
        self._role_locks: RefcountedLockMap[str] = RefcountedLockMap()
        # Serialises hydration so two concurrent callers read the durable set
        # once between them rather than racing to publish it.
        self._hydrate_lock = asyncio.Lock()

    def attach_persistence(self, *, request_repo: HiringRequestRepository) -> None:
        """Attach the durable request repo after boot.

        The service is built in the construction phase before
        persistence exists; this is called from the on-startup wiring
        hook. Pair with :meth:`hydrate`.
        """
        self._request_repo = request_repo
        # A new store is a new durable set, so whatever the in-memory set
        # holds is no longer a reflection of it.
        self._hydrated = False

    async def ensure_hydrated(self) -> None:
        """Hydrate if a previous attempt has not succeeded.

        Boot deliberately survives a hydration failure, so something has to
        keep asking; this is the seam the staffing sweep calls each pass.
        A no-op once a pass has read the durable set.
        """
        # Checked under the lock, never before it: an unguarded pre-check is a
        # check-then-act, so several sweeps arriving together would each see
        # False and each run a full paginated read. An uncontended
        # ``asyncio.Lock`` costs far less than the read it prevents.
        async with self._hydrate_lock:
            if self._hydrated:
                return
            await self._hydrate_locked()

    async def hydrate(self) -> None:
        """Load durable in-flight requests into the in-memory set.

        Idempotent and a no-op when no repository is attached.
        """
        async with self._hydrate_lock:
            await self._hydrate_locked()

    async def _hydrate_locked(self) -> None:
        """Perform the hydration pass; caller holds ``_hydrate_lock``."""
        if self._request_repo is None:
            # Nothing durable to reflect, so the in-memory set is already
            # the whole truth and a later pass has nothing to recover.
            self._hydrated = True
            return
        loaded = await read_all(self._request_repo)
        # Merged, not replaced, and the in-memory entry wins: the paginated
        # read above spans awaits, and a request created or transitioned
        # during it is newer than anything the durable pages carry. Replacing
        # the mapping would drop it, and the hire it represents would then be
        # opened a second time by whatever next asked whether one was
        # in flight.
        self._requests = {**loaded, **self._requests}
        self._hydrated = True
        logger.info(HR_HIRING_REQUESTS_HYDRATED, requests=len(loaded))

    async def _store(
        self,
        request: HiringRequest,
        *,
        require_persist: bool = False,
    ) -> None:
        """Update the in-memory set and persist the request.

        A persistence-less boot (no repo) stays in-memory only and never
        raises, since nothing is rehydrated on restart.

        Raises:
            HiringError: If ``require_persist`` and the durable save fails.
        """
        self._requests[str(request.id)] = request
        if self._request_repo is None:
            return
        await save_request(self._request_repo, request, require_persist=require_persist)

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
            HiringAlreadyInFlightError: If a hire for a gate role is already
                on its way to an agent. Enforced here rather than at each
                caller so the invariant has one owner: the staffing sweep
                and the scaler both open hires, and only one of them checked.
            HiringError: If the related operation fails.
        """
        # Role-keyed, and held across the check AND the store: the guard below
        # is a check-then-create, and the staffing sweep and the scaler both
        # reach it concurrently. Unserialised, both observe no in-flight
        # request and both create one, which is two approval items for the one
        # role the invariant exists to keep singular.
        async with self._role_locks.acquire(str(role)):
            if role_is_gate_role(str(role)) and (
                in_flight := self.find_in_flight_request_for_role(str(role))
            ):
                msg = (
                    f"A hire for {role!r} is already in flight as request "
                    f"{in_flight.id} ({in_flight.status.value})"
                )
                logger.info(
                    HR_HIRING_REQUEST_INVALID,
                    role=str(role),
                    request_id=str(in_flight.id),
                    request_status=in_flight.status.value,
                    error=msg,
                )
                raise HiringAlreadyInFlightError(msg)
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
                #
                # The pair is still proposed. Nobody is here to override it,
                # but the binding travels with the request either way, and an
                # auto-approved hire with none would fail at instantiation for
                # a decision that was never put to anybody.
                proposal = await self._propose_models(candidate)
                updated = request.model_copy(
                    update={
                        "status": HiringRequestStatus.APPROVED,
                        "selected_candidate_id": candidate_id,
                        "bound_model_ref": recommended_ref(proposal),
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

        The pair the hire would run on is proposed here rather than read from
        a standing setting, and the recommendation is stamped onto the request
        so an approval taken without an explicit pick still has a binding to
        instantiate.

        Args:
            request: The hiring request.
            candidate: The candidate to approve.
            candidate_id: ID of the candidate.

        Returns:
            Updated request with approval metadata.
        """
        assert self._approval_store is not None  # noqa: S101
        approval_id = str(uuid4())
        proposal = await self._propose_models(candidate)
        await self._approval_store.add(
            build_approval(
                request,
                candidate,
                candidate_id=candidate_id,
                approval_id=approval_id,
                proposal=proposal,
            )
        )
        return request.model_copy(
            update={
                "selected_candidate_id": candidate_id,
                "approval_id": approval_id,
                "bound_model_ref": recommended_ref(proposal),
            },
        )

    async def _propose_models(self, candidate: CandidateCard) -> HireModelProposal:
        """Offer the pairs this candidate could be hired onto.

        Returns:
            The proposal, empty and carrying its reason when the operator has
            configured nothing this role can use.
        """
        return await propose_models(
            candidate,
            catalogue=self._provider_catalogue,
            resolver=self._config_resolver,
        )

    async def bind_model(self, request_id: str, model_ref: str) -> HiringRequest:
        """Record the pair an operator picked on the approval.

        The override half of the proposal: the approval offers the pairs, and
        this is where the one the operator actually chose becomes the binding
        the hire is instantiated with. Idempotent by construction, since it
        writes a value rather than moving a state.

        Args:
            request_id: The request the approval decides.
            model_ref: The chosen pair, in MODEL_REF form.

        Returns:
            The updated request.

        Raises:
            HiringError: When no such request is in flight, or the chosen
                value does not name both halves of a pair.
        """
        # Parsed here, not only at the controller that happens to check its
        # value against the approval's options: this method is the service
        # boundary and is callable on its own, and a provider-less value
        # persisted as a binding is an agent bound to a connection nobody
        # named. Stored canonically so what comes back out is what the rest
        # of the system reads everywhere else.
        parsed = parse_model_ref(model_ref)
        if not parsed.is_bound:
            msg = (
                f"Hiring request {request_id!r} cannot bind {model_ref!r}: a"
                " binding names both a provider connection and a model id"
            )
            raise HiringError(msg)
        async with self._request_locks.acquire(request_id):
            request = self._requests.get(request_id)
            if request is None:
                msg = f"Hiring request {request_id!r} not found"
                raise HiringError(msg)
            updated = request.model_copy(
                update={"bound_model_ref": NotBlankStr(serialize_model_ref(parsed))}
            )
            await self._store(updated, require_persist=True)
            return updated

    def find_by_approval_id(self, approval_id: str) -> HiringRequest | None:
        """Find the in-flight request an approval item decides.

        The in-flight set is the authority: it is rehydrated from the
        repository at startup, so a decision that arrives after a restart
        still finds its request.

        Args:
            approval_id: The decided approval item's id.

        Returns:
            The request carrying that approval, or ``None`` when none does.
        """
        return by_approval_id(self._requests, approval_id)

    def find_in_flight_request_for_role(self, role: str) -> HiringRequest | None:
        """Find a request for *role* that is still on its way to an agent.

        Args:
            role: The role name being staffed.

        Returns:
            The in-flight request, or ``None`` when no hire is under way.
        """
        return in_flight_for_role(self._requests, role)

    def get_request(self, request_id: str) -> HiringRequest | None:
        """Return the tracked request with *request_id*.

        Args:
            request_id: The hiring request to look up.

        Returns:
            The request, or ``None`` when nothing tracks that id.
        """
        return self._requests.get(request_id)

    def find_approved_requests(self) -> tuple[HiringRequest, ...]:
        """Return every request a human approved that has not been hired yet.

        Returns:
            The approved-but-not-instantiated requests, oldest first so a
            sweep applies decisions in the order they were made.
        """
        return approved_not_instantiated(self._requests)

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
            HiringError: If instantiation fails, including when the approved
                request carries no model binding.
        """
        async with self._request_locks.acquire(str(request.id)):
            request = self._get_request(str(request.id))
            validate_instantiable(request)
            candidate = select_candidate(request)

            identity = build_agent_identity(
                candidate,
                request=request,
                model=await resolve_hire_model(
                    request, catalogue=self._provider_catalogue
                ),
                status=(
                    AgentStatus.ONBOARDING
                    if self._onboarding_service is not None
                    else AgentStatus.ACTIVE
                ),
            )
            await register_agent(self._registry, identity, request)
            await self._apply_instantiated_status(request)

        # Onboarding runs outside the lock: it is non-fatal and should
        # not hold up other pipeline steps on the same request.
        await try_onboard(self._onboarding_service, identity)

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
