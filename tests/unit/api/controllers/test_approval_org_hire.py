"""Unit tests for the org-hire approval flow.

The tail this covers is the one that used to stop at the approval row: a
human said yes and nothing registered an agent.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_org_hire import try_org_hire_resume
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ServiceUnavailableError
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
    action_type: str = ActionType.ORG_HIRE.value,
    new_hire_model: str = bound_ref(),
    link_request: bool = True,
    decision_reason: str | None = None,
) -> tuple[AppState, HiringService, AgentRegistryService, HiringRequest | None]:
    """Stand up an approvals state around one submitted hiring request.

    Args:
        action_type: Action type the approval item carries.
        new_hire_model: Stored ``hr.new_hire_model`` value.
        link_request: Whether the hiring request carries the approval id, so
            a test can exercise the orphaned-approval path.
        decision_reason: Reason recorded on the approval item.

    Returns:
        The app state, the hiring service, the registry, and the submitted
        request (``None`` when it was deliberately left unlinked).
    """
    store = ApprovalStore()
    await store.add(
        _approval("appr-1", action_type=action_type, decision_reason=decision_reason)
    )
    registry = AgentRegistryService()
    hiring = HiringService(
        registry=registry,
        approval_store=store,
        config_resolver=model_ref_resolver(default=new_hire_model),
    )
    request = await hiring.create_request(
        requested_by=NotBlankStr("staffing"),
        department=NotBlankStr("quality-assurance"),
        role=NotBlankStr("Completion Reviewer"),
        reason=NotBlankStr("No agent holds the Completion Reviewer role"),
    )
    with_candidate = await hiring.generate_candidate(request)
    submitted = await hiring.submit_for_approval(
        with_candidate, str(with_candidate.candidates[0].id)
    )
    if link_request:
        # The approval id the service minted is not the one the seeded item
        # carries, so re-key the in-flight request onto the seeded approval.
        hiring._requests[str(submitted.id)] = submitted.model_copy(
            update={"approval_id": sid("appr-1")}
        )
    else:
        hiring._requests[str(submitted.id)] = submitted.model_copy(
            update={"approval_id": sid("other-approval")}
        )
    state = make_app_state(
        approval_store=store, hiring_service=hiring, agent_registry=registry
    )
    return state, hiring, registry, (submitted if link_request else None)


class TestOrgHireResume:
    async def test_a_non_hiring_approval_is_inert(self) -> None:
        state, hiring, registry, _ = await _seed(action_type="code:write")
        handled = await try_org_hire_resume(
            state, sid("appr-1"), approved=True, decided_by=_DECIDER
        )
        assert handled is False
        assert await registry.list_active() == ()
        assert all(
            r.status is HiringRequestStatus.PENDING for r in hiring._requests.values()
        )

    async def test_approving_registers_the_agent(self) -> None:
        state, hiring, registry, submitted = await _seed()
        assert submitted is not None
        handled = await try_org_hire_resume(
            state, sid("appr-1"), approved=True, decided_by=_DECIDER
        )
        assert handled is True
        roster = await registry.list_active()
        assert len(roster) == 1
        hired = roster[0]
        assert str(hired.role) == "Completion Reviewer"
        assert str(hired.department) == "quality-assurance"
        assert hired.status is AgentStatus.ACTIVE
        # The hire runs on the operator's declared pair, not an invented one.
        assert str(hired.model.provider) == TEST_PROVIDER
        assert str(hired.model.model_id) == TEST_MODEL_ID
        assert (
            hiring._requests[str(submitted.id)].status
            is HiringRequestStatus.INSTANTIATED
        )

    async def test_rejecting_registers_nobody(self) -> None:
        state, hiring, registry, submitted = await _seed(
            decision_reason="Budget frozen this quarter"
        )
        assert submitted is not None
        handled = await try_org_hire_resume(
            state, sid("appr-1"), approved=False, decided_by=_DECIDER
        )
        assert handled is True
        assert await registry.list_active() == ()
        assert (
            hiring._requests[str(submitted.id)].status is HiringRequestStatus.REJECTED
        )

    async def test_an_orphaned_hiring_approval_fails_loud(self) -> None:
        state, _, registry, _ = await _seed(link_request=False)
        with pytest.raises(HiringError, match="No hiring request found"):
            await try_org_hire_resume(
                state, sid("appr-1"), approved=True, decided_by=_DECIDER
            )
        assert await registry.list_active() == ()

    async def test_an_unbound_new_hire_pair_refuses_rather_than_inventing_one(
        self,
    ) -> None:
        state, hiring, registry, submitted = await _seed(new_hire_model="")
        assert submitted is not None
        with pytest.raises(ServiceUnavailableError, match="new_hire_model"):
            await try_org_hire_resume(
                state, sid("appr-1"), approved=True, decided_by=_DECIDER
            )
        assert await registry.list_active() == ()
        # The decision itself stands, so the reconciler can finish the hire
        # once the operator binds a pair. It is not silently re-openable.
        assert (
            hiring._requests[str(submitted.id)].status is HiringRequestStatus.APPROVED
        )
