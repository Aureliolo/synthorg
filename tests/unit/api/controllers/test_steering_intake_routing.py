"""Tests for conversational steering routing at the approval gate (Flow 0).

An approved ``CONVERSATIONAL_INTAKE`` approval carrying the steering-directive
metadata must route to ``SteeringService.issue`` (not the work-pipeline path),
issuing the directive into the project brain. A rejected one is owned here too
but issues nothing, and a non-steering approval is disowned so the caller falls
through to the work-proposal / parked-context flows.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._conversational_resume import (
    try_conversational_intake_resume,
)
from synthorg.api.state import AppState
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalSource,
    ApprovalStatus,
    InterventionKind,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.intervention import NoOpSupersessionProposer, SteeringService
from synthorg.engine.intervention.models import (
    STEERING_INTAKE_KIND_KEY,
    STEERING_INTAKE_PROJECT_KEY,
    STEERING_INTAKE_TEXT_KEY,
)
from synthorg.meta.chief_of_staff._intake_parking import (
    is_conversational_steering,
    resume_conversational_steering,
)
from tests._shared import LoopAsyncClient, as_uuid
from tests._shared.steering import FakeBrainService
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 9, 0, 0, tzinfo=UTC)


class _StubTaskEngine:
    """Conversational steering issues in NONE supersede mode -- never cancels."""

    async def cancel_task(
        self, task_id: str, *, requested_by: str, reason: str
    ) -> tuple[None, None]:
        msg = "conversational steering must not cancel tasks"
        raise AssertionError(msg)


def _wire_steering(
    app_state: AppState, persistence: FakePersistenceBackend
) -> SteeringService:
    """Wire a fake-brain-backed steering service onto the cockpit slice.

    Returns:
        The wired :class:`SteeringService`.
    """
    service = SteeringService(
        brain_service=FakeBrainService(persistence.project_brain),  # type: ignore[arg-type]
        brain_repo=persistence.project_brain,
        task_engine=_StubTaskEngine(),  # type: ignore[arg-type]
        proposer=NoOpSupersessionProposer(),
    )
    app_state.wire(CockpitStateSlice, steering_service=service)
    return service


def _steering_item(*, status: ApprovalStatus = ApprovalStatus.PENDING) -> ApprovalItem:
    """Build a parked conversational steering approval item.

    Returns:
        The :class:`ApprovalItem` carrying the steering-directive metadata.
    """
    return ApprovalItem(
        id=as_uuid("appr-steer-1"),
        action_type=NotBlankStr("conversational:steer"),
        title=NotBlankStr("Steer checkout: redirect"),
        description=NotBlankStr("use Postgres not Mongo"),
        requested_by=NotBlankStr("ceo"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=ApprovalSource.CONVERSATIONAL_INTAKE,
        status=status,
        created_at=_NOW,
        metadata={
            "conversation_id": "conv-1",
            STEERING_INTAKE_KIND_KEY: "redirect",
            STEERING_INTAKE_PROJECT_KEY: "checkout",
            STEERING_INTAKE_TEXT_KEY: "use Postgres not Mongo",
        },
    )


class TestIsConversationalSteering:
    def test_steering_item_recognised(self) -> None:
        assert is_conversational_steering(_steering_item()) is True

    def test_none_item_rejected(self) -> None:
        assert is_conversational_steering(None) is False

    def test_work_intake_without_marker_rejected(self) -> None:
        work_item = ApprovalItem(
            id=as_uuid("appr-work-1"),
            action_type=NotBlankStr("conversational:create_work"),
            title=NotBlankStr("Build the page"),
            description=NotBlankStr("a marketing page"),
            requested_by=NotBlankStr("ceo"),
            risk_level=ApprovalRiskLevel.MEDIUM,
            source=ApprovalSource.CONVERSATIONAL_INTAKE,
            status=ApprovalStatus.PENDING,
            created_at=_NOW,
            metadata={"conversation_id": "conv-1", "proposal_id": "p1"},
        )
        assert is_conversational_steering(work_item) is False

    def test_non_conversational_source_rejected(self) -> None:
        review_item = _steering_item().model_copy(
            update={"source": ApprovalSource.REVIEW_GATE}
        )
        assert is_conversational_steering(review_item) is False


class TestResumeConversationalSteering:
    async def test_approved_steering_issues_directive(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        service = _wire_steering(app_state, fake_persistence)

        owned = await resume_conversational_steering(
            app_state, _steering_item(), approved=True
        )

        assert owned is True
        active = await service.list_active(project_id=NotBlankStr("checkout"))
        assert len(active) == 1
        directive = active[0]
        assert directive.kind is InterventionKind.REDIRECT
        assert directive.text == "use Postgres not Mongo"
        assert directive.author == "ceo"

    async def test_rejected_steering_owned_but_issues_nothing(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        service = _wire_steering(app_state, fake_persistence)

        owned = await resume_conversational_steering(
            app_state, _steering_item(), approved=False
        )

        assert owned is True
        active = await service.list_active(project_id=NotBlankStr("checkout"))
        assert active == ()

    async def test_non_steering_item_disowned(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        _wire_steering(app_state, fake_persistence)
        review_item = _steering_item().model_copy(
            update={"source": ApprovalSource.REVIEW_GATE}
        )

        owned = await resume_conversational_steering(
            app_state, review_item, approved=True
        )

        assert owned is False

    async def test_none_item_disowned(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        _wire_steering(app_state, fake_persistence)

        owned = await resume_conversational_steering(app_state, None, approved=True)

        assert owned is False

    async def test_unwired_service_raises_503_on_approve(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # An approved directive that cannot execute (steering service
        # unwired) is a hard misconfiguration, not a silent no-op.
        app_state = async_test_client.app.state.app_state
        assert app_state.slice(CockpitStateSlice).steering_service is None
        with pytest.raises(ServiceUnavailableError):
            await resume_conversational_steering(
                app_state, _steering_item(), approved=True
            )


class TestApprovalGateRouting:
    """The Flow 0 ordering guard: a steering approval must route to the
    steering service BEFORE the work-proposal lookup, which would otherwise
    swallow it as a no-op (no proposal row exists) and never issue it."""

    async def test_steering_approval_issues_via_gate_flow0(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
        approval_store: ApprovalStore,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        service = _wire_steering(app_state, fake_persistence)
        item = _steering_item()
        await approval_store.add(item)

        owned = await try_conversational_intake_resume(
            app_state, str(item.id), approved=True
        )

        assert owned is True
        active = await service.list_active(project_id=NotBlankStr("checkout"))
        assert len(active) == 1
        assert active[0].text == "use Postgres not Mongo"

    async def test_rejected_steering_approval_issues_nothing_via_gate(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
        approval_store: ApprovalStore,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        service = _wire_steering(app_state, fake_persistence)
        item = _steering_item()
        await approval_store.add(item)

        owned = await try_conversational_intake_resume(
            app_state, str(item.id), approved=False
        )

        assert owned is True
        assert await service.list_active(project_id=NotBlankStr("checkout")) == ()
