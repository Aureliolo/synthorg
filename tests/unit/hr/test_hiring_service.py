"""Tests for HiringService."""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.enums import AgentStatus
from synthorg.hr.enums import HiringRequestStatus
from synthorg.hr.errors import (
    HiringApprovalRequiredError,
    HiringError,
    HiringRejectedError,
    InvalidCandidateError,
)
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.onboarding_service import OnboardingService
from synthorg.hr.registry import AgentRegistryService
from tests.unit.hr.conftest import make_candidate_card, make_hiring_request


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
            level="mid",
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
            level="senior",
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
            level="mid",
            reason="Budget-constrained hire",
            budget_limit_monthly=100.0,
        )
        assert req.budget_limit_monthly == 100.0

    async def test_create_request_invalid_seniority_raises(
        self,
        hiring_service: HiringService,
    ) -> None:
        """Invalid seniority level string raises HiringError."""
        with pytest.raises(HiringError, match="Invalid seniority level"):
            await hiring_service.create_request(
                requested_by="cto",
                department="engineering",
                role="developer",
                level="invalid_level",
                reason="Invalid level test",
            )


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
            level="mid",
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
            level="mid",
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
            level="mid",
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
            level="mid",
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
            level="mid",
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
            level="mid",
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
        card = make_candidate_card(candidate_id="cand-001")
        req = make_hiring_request(
            status=HiringRequestStatus.REJECTED,
            selected_candidate_id="cand-001",
            candidates=(card,),
        )
        # Register request in service's internal store so _get_request finds it.
        hiring_service._requests[str(req.id)] = req
        with pytest.raises(HiringRejectedError, match="rejected"):
            await hiring_service.instantiate_agent(req)

    async def test_instantiate_pending_raises(
        self,
        hiring_service: HiringService,
    ) -> None:
        card = make_candidate_card(candidate_id="cand-001")
        req = make_hiring_request(
            status=HiringRequestStatus.PENDING,
            selected_candidate_id="cand-001",
            candidates=(card,),
        )
        # Register request in service's internal store so _get_request finds it.
        hiring_service._requests[str(req.id)] = req
        with pytest.raises(
            HiringApprovalRequiredError,
            match="requires approval",
        ):
            await hiring_service.instantiate_agent(req)

    async def test_approved_without_candidate_rejected_by_model(
        self,
    ) -> None:
        """APPROVED requests without selected_candidate_id are now
        rejected by the model validator, so instantiate_agent
        can never receive such a request."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="selected_candidate_id"):
            make_hiring_request(
                status=HiringRequestStatus.APPROVED,
                selected_candidate_id=None,
            )

    async def test_instantiate_already_instantiated_raises(
        self,
        hiring_service: HiringService,
    ) -> None:
        """Re-instantiation of an already INSTANTIATED request raises HiringError."""
        req = await hiring_service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            level="mid",
            reason="Re-instantiation guard test",
        )
        updated = await hiring_service.generate_candidate(req)
        candidate_id = str(updated.candidates[0].id)
        approved = await hiring_service.submit_for_approval(updated, candidate_id)
        await hiring_service.instantiate_agent(approved)
        # Fetch the now-INSTANTIATED request and try again.
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
        )
        req = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            level="mid",
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
