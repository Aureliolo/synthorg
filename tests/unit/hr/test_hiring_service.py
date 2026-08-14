"""Tests for HiringService."""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus, HiringRequestStatus
from synthorg.hr.errors import (
    HiringAlreadyInFlightError,
    HiringApprovalRequiredError,
    HiringError,
    HiringRejectedError,
    InvalidCandidateError,
)
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from tests._shared import sid
from tests._shared.model_binding import TEST_PROVIDER, bound_ref, model_ref_resolver
from tests.unit.hr.conftest import make_hiring_request


@pytest.mark.unit
class TestHiringServiceCreateRequest:
    """HiringService.create_request tests."""

    async def test_create_request_returns_hiring_request(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Need more devs",
        )
        assert req.status == HiringRequestStatus.PENDING
        assert req.requested_by == "cto"
        assert req.department == "engineering"
        assert req.role == "developer"

    async def test_create_request_with_skills(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            required_skills=("python", "rust"),
            reason="Need senior devs",
        )
        assert len(req.required_skills) == 2

    async def test_create_request_with_budget_limit(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Budget-constrained hire",
            budget_limit_monthly=100.0,
        )
        assert req.budget_limit_monthly == 100.0

    async def test_a_second_gate_role_hire_is_refused(
        self,
        hiring_service: HiringService,
    ) -> None:
        """One question to the operator, not one per caller who noticed.

        Two callers open hires (the staffing sweep and the scaler) and only
        one of them checked, so the invariant lives here instead.
        """
        first = await hiring_service.create_request(
            requested_by="staffing",
            department="quality-assurance",
            role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
            reason="Nobody holds it",
        )

        with pytest.raises(HiringAlreadyInFlightError, match=str(first.id)):
            await hiring_service.create_request(
                requested_by="scaling_service",
                department="quality-assurance",
                role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
                reason="Review queue is deep",
            )

    async def test_a_second_ordinary_hire_is_allowed(
        self,
        hiring_service: HiringService,
    ) -> None:
        """Headcount is the scaler's decision, not a duplicate to collapse.

        Two teams wanting a backend developer is two hires; only the roles
        held org-wide have nothing to gain from a second request.
        """
        await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Need more devs",
        )
        second = await hiring_service.create_request(
            requested_by="cto",
            department="platform",
            role="developer",
            reason="Need more devs here too",
        )
        assert second.status is HiringRequestStatus.PENDING

    async def test_a_rejected_gate_role_hire_may_be_asked_again(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """The operator answered a question, not the role's future."""
        hiring_service = HiringService(
            registry=registry, approval_store=ApprovalStore()
        )
        first = await hiring_service.create_request(
            requested_by="staffing",
            department="quality-assurance",
            role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
            reason="Nobody holds it",
        )
        with_candidate = await hiring_service.generate_candidate(first)
        submitted = await hiring_service.submit_for_approval(
            with_candidate, str(with_candidate.candidates[0].id)
        )
        await hiring_service.reject_request(str(submitted.id), decided_by="operator")

        again = await hiring_service.create_request(
            requested_by="staffing",
            department="quality-assurance",
            role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
            reason="Still nobody holds it",
        )
        assert again.status is HiringRequestStatus.PENDING


@pytest.mark.unit
class TestHiringServiceGenerateCandidate:
    """HiringService.generate_candidate tests."""

    async def test_generate_candidate_appends(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Expand team",
        )
        updated = await hiring_service.generate_candidate(req)
        assert len(updated.candidates) == 1
        candidate = updated.candidates[0]
        assert candidate.role == "developer"
        assert candidate.department == "engineering"

    async def test_generate_multiple_candidates(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Expand team",
        )
        updated = await hiring_service.generate_candidate(req)
        updated = await hiring_service.generate_candidate(updated)
        assert len(updated.candidates) == 2


@pytest.mark.unit
class TestHiringServiceSubmitForApproval:
    """HiringService.submit_for_approval tests."""

    async def test_auto_approve_without_store(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Auto-approve test",
        )
        updated = await hiring_service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)
        approved = await hiring_service.submit_for_approval(updated, candidate_id)
        assert approved.status == HiringRequestStatus.APPROVED
        assert approved.selected_candidate_id == candidate_id

    async def test_submit_with_approval_store_creates_item(
        self,
        registry: AgentRegistryService,
    ) -> None:
        store = ApprovalStore()
        service = HiringService(registry=registry, approval_store=store)
        req = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Approval required test",
        )
        updated = await service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)
        submitted = await service.submit_for_approval(updated, candidate_id)
        # Should not be auto-approved.
        assert submitted.status == HiringRequestStatus.PENDING
        assert submitted.selected_candidate_id == candidate_id
        assert submitted.approval_id is not None
        # Approval item should exist in store.
        item = await store.get(submitted.approval_id)
        assert item is not None

    async def test_submit_invalid_candidate_raises(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Bad candidate test",
        )
        updated = await hiring_service.generate_candidate(req)
        with pytest.raises(InvalidCandidateError, match="not found"):
            await hiring_service.submit_for_approval(updated, "nonexistent-id")


@pytest.mark.unit
class TestHiringServiceInstantiateAgent:
    """HiringService.instantiate_agent tests."""

    async def test_instantiate_approved_creates_agent(
        self,
        hiring_service: HiringService,
        registry: AgentRegistryService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Instantiate test",
        )
        updated = await hiring_service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)
        approved = await hiring_service.submit_for_approval(updated, candidate_id)
        identity = await hiring_service.instantiate_agent(approved)
        # Without onboarding_service, agent starts as ACTIVE.
        assert identity.status == AgentStatus.ACTIVE
        assert identity.role == "developer"
        assert identity.department == "engineering"
        # Agent should be in registry.
        fetched = await registry.get(str(identity.id))
        assert fetched is not None

    async def test_instantiate_rejected_raises(
        self,
        hiring_service: HiringService,
    ) -> None:
        # Driven through the lifecycle rather than seeded: the guard reads
        # the TRACKED request, so a test that plants one is testing its own
        # plant. ``instantiate_agent`` is given the pre-rejection copy on
        # purpose -- what it acts on is what the service tracks.
        req = await hiring_service.create_request(
            requested_by=NotBlankStr("staffing"),
            department=NotBlankStr("engineering"),
            role=NotBlankStr("Backend Developer"),
            reason=NotBlankStr("Team is short-handed"),
        )
        await hiring_service.reject_request(
            str(req.id), decided_by="operator", reason="Budget frozen"
        )

        with pytest.raises(HiringRejectedError, match="rejected"):
            await hiring_service.instantiate_agent(req)

    async def test_instantiate_pending_raises(
        self,
        hiring_service: HiringService,
    ) -> None:
        req = await hiring_service.create_request(
            requested_by=NotBlankStr("staffing"),
            department=NotBlankStr("engineering"),
            role=NotBlankStr("Backend Developer"),
            reason=NotBlankStr("Team is short-handed"),
        )

        with pytest.raises(
            HiringApprovalRequiredError,
            match="requires approval",
        ):
            await hiring_service.instantiate_agent(req)

    async def test_approved_without_candidate_rejected_by_model(
        self,
    ) -> None:
        """The model validator rejects an APPROVED request with no
        selected_candidate_id, so instantiate_agent can never receive one."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="selected_candidate_id"):
            make_hiring_request(
                status=HiringRequestStatus.APPROVED,
                selected_candidate_id=None,
            )

    async def test_reinstantiate_rejects_already_instantiated(
        self,
        hiring_service: HiringService,
    ) -> None:
        """Re-instantiating a request rejects (no double-instantiation).

        The terminal INSTANTIATED request is retained in the in-memory cache
        (this service has no persistence read path), so the second attempt
        hits the already-instantiated status guard rather than a misleading
        not-found lookup. The per-request lock still self-evicts once the
        instantiate step releases it (no holders left).
        """
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Re-instantiation guard test",
        )
        updated = await hiring_service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)
        approved = await hiring_service.submit_for_approval(updated, candidate_id)
        await hiring_service.instantiate_agent(approved)
        # The terminal request stays cached as INSTANTIATED; only its lock
        # auto-evicts once the instantiate step releases it (no holders left).
        assert str(approved.id) in hiring_service._requests
        assert len(hiring_service._request_locks) == 0
        with pytest.raises(HiringError, match="already instantiated"):
            await hiring_service.instantiate_agent(approved)

    async def test_instantiate_triggers_onboarding(
        self,
        registry: AgentRegistryService,
        onboarding_service: OnboardingService,
    ) -> None:
        service = HiringService(
            registry=registry,
            onboarding_service=onboarding_service,
            config_resolver=model_ref_resolver(),
        )
        req = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Onboarding trigger test",
        )
        updated = await service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)
        approved = await service.submit_for_approval(updated, candidate_id)
        identity = await service.instantiate_agent(approved)
        # Onboarding should have started.
        checklist = await onboarding_service.get_checklist(str(identity.id))
        assert checklist is not None
        assert checklist.is_complete is False


@pytest.mark.unit
class TestHiringRequestStatusTransitionedLogs:
    """Status-transition logs fire for every persisted hop."""

    async def test_auto_approve_emits_pending_to_approved_transition(
        self,
        hiring_service: HiringService,
    ) -> None:
        import structlog.testing

        from synthorg.observability.events.hr import (
            HIRING_REQUEST_STATUS_TRANSITIONED,
        )

        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Auto-approve transition test",
        )
        updated = await hiring_service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)

        with structlog.testing.capture_logs() as captured:
            approved = await hiring_service.submit_for_approval(updated, candidate_id)

        # Filter to the transition event only.
        transitions = [
            entry
            for entry in captured
            if entry.get("event") == HIRING_REQUEST_STATUS_TRANSITIONED
        ]
        assert len(transitions) == 1, captured
        entry = transitions[0]
        assert entry["log_level"] == "info"
        assert entry["request_id"] == str(approved.id)
        assert entry["from_status"] == HiringRequestStatus.PENDING.value
        assert entry["to_status"] == HiringRequestStatus.APPROVED.value

    async def test_manual_approval_branch_does_not_emit_transition(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """Approval-store branch keeps PENDING; no status hop to log."""
        import structlog.testing

        from synthorg.observability.events.hr import (
            HIRING_REQUEST_STATUS_TRANSITIONED,
        )

        store = ApprovalStore()
        service = HiringService(registry=registry, approval_store=store)
        req = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Manual-approval transition test",
        )
        updated = await service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)

        with structlog.testing.capture_logs() as captured:
            await service.submit_for_approval(updated, candidate_id)

        transitions = [
            entry
            for entry in captured
            if entry.get("event") == HIRING_REQUEST_STATUS_TRANSITIONED
        ]
        # Status is still PENDING -- no hop, no log.
        assert transitions == []

    async def test_instantiate_emits_approved_to_instantiated_transition(
        self,
        hiring_service: HiringService,
    ) -> None:
        import structlog.testing

        from synthorg.observability.events.hr import (
            HIRING_REQUEST_STATUS_TRANSITIONED,
        )

        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Instantiate transition test",
        )
        updated = await hiring_service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)
        approved = await hiring_service.submit_for_approval(updated, candidate_id)

        with structlog.testing.capture_logs() as captured:
            await hiring_service.instantiate_agent(approved)

        transitions = [
            entry
            for entry in captured
            if entry.get("event") == HIRING_REQUEST_STATUS_TRANSITIONED
        ]
        assert len(transitions) == 1, captured
        entry = transitions[0]
        assert entry["log_level"] == "info"
        assert entry["request_id"] == str(approved.id)
        assert entry["from_status"] == HiringRequestStatus.APPROVED.value
        assert entry["to_status"] == HiringRequestStatus.INSTANTIATED.value


@pytest.mark.unit
class TestHiringServiceDecisions:
    """The step between a submitted request and an instantiated agent."""

    async def _submitted(
        self,
        service: HiringService,
        *,
        role: str = "developer",
    ) -> tuple[str, str]:
        """Create, generate and submit one request.

        Args:
            service: The service under test.
            role: Role the request is for.

        Returns:
            The request id and the approval id it was submitted under.
        """
        req = await service.create_request(
            requested_by="cto",
            department="engineering",
            role=role,
            reason="Decision surface test",
        )
        updated = await service.generate_candidate(req)
        submitted = await service.submit_for_approval(
            updated, str(updated.candidates[0].id)
        )
        assert submitted.approval_id is not None
        return str(submitted.id), submitted.approval_id

    async def test_find_by_approval_id_finds_the_submitted_request(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, approval_id = await self._submitted(service)
        found = service.find_by_approval_id(approval_id)
        assert found is not None
        assert str(found.id) == request_id

    async def test_find_by_approval_id_misses_for_a_foreign_approval(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """A non-hiring approval must read as a miss, never an error.

        Every approval decision walks this lookup, so raising here would
        turn each unrelated decision into a failure.
        """
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        await self._submitted(service)
        assert service.find_by_approval_id(sid("some-other-approval")) is None

    async def test_in_flight_lookup_returns_the_undecided_one(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, _ = await self._submitted(service, role="Completion Reviewer")
        found = service.find_in_flight_request_for_role("Completion Reviewer")
        assert found is not None
        assert str(found.id) == request_id
        assert service.find_in_flight_request_for_role("Red Team") is None

    async def test_in_flight_lookup_ignores_a_rejected_one(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """A declined request must not suppress the next ask for that role."""
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, _ = await self._submitted(service, role="Completion Reviewer")
        await service.reject_request(
            request_id, decided_by="operator", reason="not now"
        )
        assert service.find_in_flight_request_for_role("Completion Reviewer") is None

    async def test_in_flight_lookup_still_sees_an_approved_one(
        self,
        registry: AgentRegistryService,
    ) -> None:
        """An approved-but-uninstantiated hire is still under way.

        Approval and instantiation are separate steps, so a request stuck
        between them is the answer to "is a hire already open for this role".
        Reading it as closed opens a duplicate approval every sweep.
        """
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, _ = await self._submitted(service, role="Completion Reviewer")
        await service.approve_request(request_id, decided_by="operator")
        found = service.find_in_flight_request_for_role("Completion Reviewer")
        assert found is not None
        assert str(found.id) == request_id
        assert found.status is HiringRequestStatus.APPROVED

    async def test_get_request_returns_the_tracked_request(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, _ = await self._submitted(service)
        found = service.get_request(request_id)
        assert found is not None
        assert str(found.id) == request_id
        assert service.get_request(sid("nothing-tracks-this")) is None

    async def test_approve_moves_pending_to_approved(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, _ = await self._submitted(service)
        approved = await service.approve_request(request_id, decided_by="operator")
        assert approved.status is HiringRequestStatus.APPROVED
        assert approved.selected_candidate_id is not None

    async def test_reject_moves_pending_to_rejected(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, _ = await self._submitted(service)
        rejected = await service.reject_request(
            request_id, decided_by="operator", reason="Budget frozen"
        )
        assert rejected.status is HiringRequestStatus.REJECTED

    async def test_deciding_an_already_decided_request_is_refused(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(registry=registry, approval_store=ApprovalStore())
        request_id, _ = await self._submitted(service)
        await service.approve_request(request_id, decided_by="operator")
        with pytest.raises(HiringError, match="not awaiting a decision"):
            await service.approve_request(request_id, decided_by="operator")
        with pytest.raises(HiringError, match="not awaiting a decision"):
            await service.reject_request(request_id, decided_by="operator")


@pytest.mark.unit
class TestHiringServiceModelBinding:
    """A hire runs on the operator's declared pair or it does not happen."""

    async def test_an_unbound_pair_refuses_instantiation(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(
            registry=registry,
            config_resolver=model_ref_resolver(default=""),
        )
        req = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Unbound pair test",
        )
        updated = await service.generate_candidate(req)
        approved = await service.submit_for_approval(
            updated, str(updated.candidates[0].id)
        )
        with pytest.raises(ServiceUnavailableError, match="new_hire_model"):
            await service.instantiate_agent(approved)
        assert await registry.list_active() == ()

    async def test_the_hire_carries_the_configured_pair(
        self,
        registry: AgentRegistryService,
    ) -> None:
        service = HiringService(
            registry=registry,
            config_resolver=model_ref_resolver(
                {("hr", "new_hire_model"): bound_ref("example-expert-001")},
                default="",
            ),
        )
        req = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Configured pair test",
        )
        updated = await service.generate_candidate(req)
        approved = await service.submit_for_approval(
            updated, str(updated.candidates[0].id)
        )
        identity = await service.instantiate_agent(approved)
        assert str(identity.model.provider) == TEST_PROVIDER
        assert str(identity.model.model_id) == "example-expert-001"
