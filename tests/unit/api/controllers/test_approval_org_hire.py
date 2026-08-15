"""Unit tests for the org-hire approval flow.

A human saying yes is only half the hire; these cover the other half, where
the approved request becomes a registered agent.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_org_hire import try_org_hire_resume
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus, HiringRequestStatus
from synthorg.hr.errors import HiringError
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.models import HiringRequest
from synthorg.hr.registry import AgentRegistryService
from synthorg.security.autonomy.enums import ActionType
from tests._shared import as_uuid, make_app_state, sid
from tests._shared.model_binding import (
    TEST_MODEL_ID,
    TEST_PROVIDER,
    bound_ref,
    model_ref_resolver,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
_DECIDER = "operator-1"


def _approval(
    approval_id: str,
    *,
    action_type: str = ActionType.ORG_HIRE.value,
    decision_reason: str | None = None,
) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=NotBlankStr(action_type),
        title=NotBlankStr("Hire a reviewer"),
        description=NotBlankStr("Nobody holds the Completion Reviewer role"),
        requested_by=NotBlankStr("staffing"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        decision_reason=(
            NotBlankStr(decision_reason) if decision_reason is not None else None
        ),
    )


async def _seed(
    *,
    new_hire_model: str = bound_ref(),
    decision_reason: str | None = None,
) -> tuple[AppState, HiringService, AgentRegistryService, HiringRequest, str]:
    """Stand up an approvals state around one submitted hiring request.

    The approval the flow decides is the one the service itself minted:
    seeding a separate item and re-keying the request onto it would test a
    link the production path never makes.

    Args:
        new_hire_model: Stored ``hr.new_hire_model`` value.
        decision_reason: Reason recorded on the approval item.

    Returns:
        The app state, the hiring service, the registry, the submitted
        request, and the approval id that decides it.
    """
    store = ApprovalStore()
    registry = AgentRegistryService()
    hiring = HiringService(
        registry=registry,
        approval_store=store,
        config_resolver=model_ref_resolver(default=new_hire_model),
    )
    request = await hiring.create_request(
        requested_by=NotBlankStr("staffing"),
        department=NotBlankStr("quality-assurance"),
        role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
        reason=NotBlankStr("No agent holds the Completion Reviewer role"),
    )
    with_candidate = await hiring.generate_candidate(request)
    submitted = await hiring.submit_for_approval(
        with_candidate, str(with_candidate.candidates[0].id)
    )
    approval_id = str(submitted.approval_id)
    if decision_reason is not None:
        item = await store.get(approval_id)
        assert item is not None
        await store.save(
            item.model_copy(update={"decision_reason": NotBlankStr(decision_reason)})
        )
    state = make_app_state(
        approval_store=store, hiring_service=hiring, agent_registry=registry
    )
    return state, hiring, registry, submitted, approval_id


def _status(hiring: HiringService, request: HiringRequest) -> HiringRequestStatus:
    """Return the tracked status of *request*.

    Returns:
        The status the service currently holds for it.
    """
    tracked = hiring.get_request(str(request.id))
    assert tracked is not None
    return tracked.status


class TestOrgHireResume:
    async def test_a_non_hiring_approval_is_inert(self) -> None:
        state, hiring, registry, submitted, _ = await _seed()
        store = ApprovalStore()
        await store.add(_approval("appr-code", action_type="code:write"))
        state = make_app_state(
            approval_store=store, hiring_service=hiring, agent_registry=registry
        )

        handled = await try_org_hire_resume(
            state, sid("appr-code"), approved=True, decided_by=_DECIDER
        )

        assert handled is False
        assert await registry.list_active() == ()
        assert _status(hiring, submitted) is HiringRequestStatus.PENDING

    async def test_approving_registers_the_agent(self) -> None:
        state, hiring, registry, submitted, approval_id = await _seed()
        handled = await try_org_hire_resume(
            state, approval_id, approved=True, decided_by=_DECIDER
        )
        assert handled is True
        roster = await registry.list_active()
        assert len(roster) == 1
        hired = roster[0]
        assert str(hired.role) == COMPLETION_REVIEWER_ROLE_NAME
        assert str(hired.department) == "quality-assurance"
        assert hired.status is AgentStatus.ACTIVE
        # The hire runs on the operator's declared pair, not an invented one.
        assert str(hired.model.provider) == TEST_PROVIDER
        assert str(hired.model.model_id) == TEST_MODEL_ID
        assert _status(hiring, submitted) is HiringRequestStatus.INSTANTIATED

    async def test_rejecting_registers_nobody(self) -> None:
        state, hiring, registry, submitted, approval_id = await _seed(
            decision_reason="Budget frozen this quarter"
        )
        handled = await try_org_hire_resume(
            state, approval_id, approved=False, decided_by=_DECIDER
        )
        assert handled is True
        assert await registry.list_active() == ()
        assert _status(hiring, submitted) is HiringRequestStatus.REJECTED

    @pytest.mark.parametrize("approved", [True, False])
    async def test_a_re_dispatched_decision_is_owned_and_finished(
        self, approved: bool
    ) -> None:
        """The crash-recovery drain re-runs decisions whose marker survived.

        A settled request refuses another decision, and the drain keeps a
        marker whose re-dispatch raised, so answering with the refusal would
        retry the same approval at every boot for the life of the org.
        """
        state, hiring, registry, submitted, approval_id = await _seed(
            decision_reason="Budget frozen this quarter"
        )
        await try_org_hire_resume(
            state, approval_id, approved=approved, decided_by=_DECIDER
        )
        settled = _status(hiring, submitted)
        roster_before = await registry.list_active()

        handled = await try_org_hire_resume(
            state, approval_id, approved=approved, decided_by=_DECIDER
        )

        assert handled is True
        assert _status(hiring, submitted) is settled
        # No second agent for one approved hire, either.
        assert await registry.list_active() == roster_before

    async def test_an_orphaned_hiring_approval_fails_loud(self) -> None:
        state, hiring, registry, _, _ = await _seed()
        store = ApprovalStore()
        await store.add(_approval("appr-orphan"))
        state = make_app_state(
            approval_store=store, hiring_service=hiring, agent_registry=registry
        )

        with pytest.raises(HiringError, match="No hiring request found"):
            await try_org_hire_resume(
                state, sid("appr-orphan"), approved=True, decided_by=_DECIDER
            )

        assert await registry.list_active() == ()

    async def test_an_unbound_new_hire_pair_refuses_rather_than_inventing_one(
        self,
    ) -> None:
        state, hiring, registry, submitted, approval_id = await _seed(new_hire_model="")
        with pytest.raises(ServiceUnavailableError, match="new_hire_model"):
            await try_org_hire_resume(
                state, approval_id, approved=True, decided_by=_DECIDER
            )
        assert await registry.list_active() == ()
        # The decision itself stands, so the reconciler can finish the hire
        # once the operator binds a pair. It is not silently re-openable.
        assert _status(hiring, submitted) is HiringRequestStatus.APPROVED
