"""Unit tests for the conversational-intake approval dispatch branch."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._conversational_resume import (
    try_conversational_intake_resume,
)
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.communication.conversation.enums import ConversationalProposalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.meta.chief_of_staff.models import ConversationalProposal
from synthorg.meta.chief_of_staff.resume_service import ConversationalResumeService
from synthorg.meta.state import MetaStateSlice
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteRepository,
)
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantRepository,
)
from synthorg.persistence.conversation_protocol import (
    ConversationRepository,
    ConversationTurnRepository,
)
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
)
from tests._shared import (
    StubWorkPipeline,
    as_uuid,
    make_app_state,
    mock_of,
    sid,
    task_from_work_item,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _work_item_json() -> str:
    return WorkItem(
        origin_adapter_id=NotBlankStr("conversational-cos"),
        source=WorkSource.CONVERSATIONAL,
        title=NotBlankStr("Build landing page"),
        raw_intent=NotBlankStr("Create the marketing page"),
        project=NotBlankStr("marketing"),
        requested_by=NotBlankStr("user-1"),
        created_at=_NOW,
    ).model_dump_json()


class _FakeProposalRepo:
    """Complete ``ConversationalProposalRepository`` double (in-memory)."""

    def __init__(self) -> None:
        self.items: dict[str, ConversationalProposal] = {}

    async def save(self, entity: ConversationalProposal, /) -> None:
        self.items[str(entity.id)] = entity

    async def get(self, entity_id: NotBlankStr, /) -> ConversationalProposal | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ConversationalProposal, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def query(
        self,
        filter_spec: ConversationalProposalFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        rows = [
            p
            for p in self.items.values()
            if filter_spec.approval_id is None
            or p.approval_id == filter_spec.approval_id
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ConversationalProposalFilterSpec) -> int:
        # Count matching rows directly: delegating to ``query`` would cap
        # the count at its default page limit and undercount past it.
        return sum(
            1
            for p in self.items.values()
            if filter_spec.approval_id is None
            or p.approval_id == filter_spec.approval_id
        )

    async def transition_if(
        self,
        /,
        entity_id: NotBlankStr,
        from_state: ConversationalProposalStatus,
        to_state: ConversationalProposalStatus,
        **updates: object,
    ) -> bool:
        cur = self.items.get(entity_id)
        if cur is None or cur.status is not from_state:
            return False
        self.items[entity_id] = cur.model_copy(update={"status": to_state})
        return True


class _FakeWorkerService:
    """Records ``dispatch_conversational_execution`` calls.

    The controller intakes synchronously then hands the decompose+execute
    spine to the worker; the background run is the worker's concern (covered
    by the worker suite), so here it is a recording no-op.
    """

    def __init__(self) -> None:
        self.dispatched: list[tuple[WorkItem, Task]] = []

    def dispatch_conversational_execution(
        self,
        *,
        work_pipeline: WorkPipeline,
        work_item: WorkItem,
        task: Task,
    ) -> None:
        del work_pipeline
        self.dispatched.append((work_item, task))

    async def execute_once(
        self,
        *,
        task_id: str,
        previous_status: str | None,
        new_status: str,
        idempotency_key: str,
        requested_by: str,
    ) -> Task:
        # Unused by the conversational-intake flow; fail loud if reached.
        msg = "execute_once not expected in the intake flow"
        raise AssertionError(msg)

    async def dispatch_resume(
        self,
        *,
        approval_id: str,
        approved: bool,
        decided_by: str,
        decision_reason: str | None,
    ) -> None:
        msg = "dispatch_resume not expected in the intake flow"
        raise AssertionError(msg)


def _make_app_state(
    *,
    approval_store: ApprovalStore,
    proposal_repo: _FakeProposalRepo | None,
    pipeline: StubWorkPipeline | None,
    worker_service: _FakeWorkerService | None = None,
    task_engine: object | None = None,
) -> AppState:
    """Build an AppState with the conversational-resume slices wired.

    The resume flow reads the approval store via
    ``slice(ApprovalStateSlice).store``, the proposal repo (through the
    resume-service facade) via
    ``slice(MetaStateSlice).conversational_resume_service``, the work
    pipeline via ``slice(EngineStateSlice).work_pipeline``, and the worker
    execution service via ``slice(RuntimeStateSlice)``. The service is wired
    only when ``proposal_repo`` is present, so a ``None`` repo exercises the
    controller's "resume service not wired" 503 path. The invite /
    participant repos are unused by the intake flow, so typed mocks stand in
    for them in the facade.
    """
    meta_fields: dict[str, object] = {}
    if proposal_repo is not None:
        meta_fields["conversational_proposal_repo"] = proposal_repo
        meta_fields["conversational_resume_service"] = ConversationalResumeService(
            proposal_repo=proposal_repo,
            invite_repo=mock_of[ConversationInviteRepository](),
            participant_repo=mock_of[ConversationParticipantRepository](),
            conversation_repo=mock_of[ConversationRepository](),
            turn_repo=mock_of[ConversationTurnRepository](),
        )
    return make_app_state(
        approval_store=approval_store,
        work_pipeline=pipeline,
        worker_execution_service=worker_service
        if worker_service is not None
        else _FakeWorkerService(),
        task_engine=task_engine,
        slices={MetaStateSlice: meta_fields},
    )


def _approval(
    approval_id: str,
    *,
    source: ApprovalSource = ApprovalSource.CONVERSATIONAL_INTAKE,
) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=NotBlankStr("conversational:create_work"),
        title=NotBlankStr("Build landing page"),
        description=NotBlankStr("Create the marketing page"),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=source,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
    )


def _proposal(approval_id: str) -> ConversationalProposal:
    return ConversationalProposal(
        id=as_uuid(f"prop-{approval_id}"),
        conversation_id=NotBlankStr("conv-1"),
        approval_id=NotBlankStr(sid(approval_id)),
        work_item_json=NotBlankStr(_work_item_json()),
        status=ConversationalProposalStatus.PENDING,
        created_at=_NOW,
    )


async def _seed(
    *,
    source: ApprovalSource = ApprovalSource.CONVERSATIONAL_INTAKE,
    with_proposal: bool = True,
    pipeline: StubWorkPipeline | None = None,
    worker_service: _FakeWorkerService | None = None,
) -> tuple[AppState, _FakeProposalRepo]:
    store = ApprovalStore()
    await store.add(_approval("a1", source=source))
    repo = _FakeProposalRepo()
    if with_proposal:
        prop = _proposal("a1")
        repo.items[str(prop.id)] = prop
    state = _make_app_state(
        approval_store=store,
        proposal_repo=repo,
        pipeline=pipeline if pipeline is not None else StubWorkPipeline(),
        worker_service=worker_service,
    )
    return state, repo


class TestConversationalIntakeResume:
    async def test_non_conversational_source_is_inert(self) -> None:
        state, _ = await _seed(source=ApprovalSource.REVIEW_GATE)
        handled = await try_conversational_intake_resume(
            state,
            sid("a1"),
            approved=True,
        )
        assert handled is False

    async def test_approve_intakes_dispatches_and_marks_executed(self) -> None:
        pipeline = StubWorkPipeline()
        worker = _FakeWorkerService()
        state, repo = await _seed(pipeline=pipeline, worker_service=worker)
        handled = await try_conversational_intake_resume(
            state,
            sid("a1"),
            approved=True,
        )
        assert handled is True
        # Intake runs synchronously; the decompose+execute spine is handed
        # to the worker to background. EXECUTED means "dispatched".
        assert len(pipeline.calls) == 1
        assert pipeline.calls[0].source is WorkSource.CONVERSATIONAL
        assert len(worker.dispatched) == 1
        assert (
            repo.items[sid("prop-a1")].status is ConversationalProposalStatus.EXECUTED
        )

    async def test_approve_stamps_task_id_on_the_approval(self) -> None:
        # The chat subscribes to the intake task's SSE stream, so the id
        # must be patched onto the decided approval for the deep link.
        pipeline = StubWorkPipeline()
        worker = _FakeWorkerService()
        store = ApprovalStore()
        await store.add(_approval("a1"))
        repo = _FakeProposalRepo()
        prop = _proposal("a1")
        repo.items[str(prop.id)] = prop
        state = _make_app_state(
            approval_store=store,
            proposal_repo=repo,
            pipeline=pipeline,
            worker_service=worker,
        )
        await try_conversational_intake_resume(state, sid("a1"), approved=True)
        stamped = await store.get(NotBlankStr(sid("a1")))
        assert stamped is not None
        assert stamped.task_id == str(worker.dispatched[0][1].id)

    async def test_reject_skips_pipeline_and_marks_rejected(self) -> None:
        pipeline = StubWorkPipeline()
        worker = _FakeWorkerService()
        state, repo = await _seed(pipeline=pipeline, worker_service=worker)
        handled = await try_conversational_intake_resume(
            state,
            sid("a1"),
            approved=False,
        )
        assert handled is True
        assert pipeline.calls == []
        assert worker.dispatched == []
        assert (
            repo.items[sid("prop-a1")].status is ConversationalProposalStatus.REJECTED
        )

    async def test_missing_proposal_is_owned_but_noop(self) -> None:
        state, _ = await _seed(with_proposal=False)
        handled = await try_conversational_intake_resume(
            state,
            sid("a1"),
            approved=True,
        )
        assert handled is True

    async def test_pipeline_unavailable_raises(self) -> None:
        store = ApprovalStore()
        await store.add(_approval("a1"))
        repo = _FakeProposalRepo()
        prop = _proposal("a1")
        repo.items[str(prop.id)] = prop
        state = _make_app_state(
            approval_store=store,
            proposal_repo=repo,
            pipeline=None,
        )
        with pytest.raises(ServiceUnavailableError):
            await try_conversational_intake_resume(
                state,
                sid("a1"),
                approved=True,
            )

    async def test_missing_proposal_repo_raises(self) -> None:
        # Hard misconfiguration: a conversational-intake approval lands
        # on a deployment where the proposal repo was never wired. The
        # gate cannot drive the decision either way, so it must raise
        # rather than silently mark the approval handled.
        store = ApprovalStore()
        await store.add(_approval("a1"))
        state = _make_app_state(
            approval_store=store,
            proposal_repo=None,
            pipeline=StubWorkPipeline(),
        )
        with pytest.raises(ServiceUnavailableError):
            await try_conversational_intake_resume(
                state,
                sid("a1"),
                approved=True,
            )

    async def test_intake_failure_reverts_executing_to_pending(self) -> None:
        # A synchronous intake failure must revert the proposal from
        # EXECUTING back to PENDING so a future approval-decision retry can
        # run; leaving it stuck in EXECUTING would silently lock the row.
        pipeline = StubWorkPipeline(intake_error=RuntimeError("boom"))
        worker = _FakeWorkerService()
        state, repo = await _seed(pipeline=pipeline, worker_service=worker)
        handled = await try_conversational_intake_resume(
            state,
            sid("a1"),
            approved=True,
        )
        assert handled is True
        assert worker.dispatched == []
        assert repo.items[sid("prop-a1")].status is ConversationalProposalStatus.PENDING

    async def test_retry_reuses_stamped_task_without_reintake(self) -> None:
        # A prior attempt that reverted to PENDING already created + stamped
        # a task on the approval; a retry must reuse it, not mint a duplicate
        # orphan. Seed the approval with a task id and a task engine that
        # resolves it, then assert intake never runs again.
        pipeline = StubWorkPipeline()
        worker = _FakeWorkerService()
        store = ApprovalStore()
        existing = _approval("a1")
        reused_task = task_from_work_item(
            WorkItem.model_validate_json(_work_item_json())
        )
        await store.add(existing.model_copy(update={"task_id": str(reused_task.id)}))
        repo = _FakeProposalRepo()
        prop = _proposal("a1")
        repo.items[str(prop.id)] = prop
        state = _make_app_state(
            approval_store=store,
            proposal_repo=repo,
            pipeline=pipeline,
            worker_service=worker,
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(return_value=reused_task)
            ),
        )
        handled = await try_conversational_intake_resume(
            state, sid("a1"), approved=True
        )
        assert handled is True
        assert pipeline.calls == []  # intake_only not re-run
        assert worker.dispatched[0][1].id == reused_task.id

    async def test_concurrent_acquire_only_one_runs_pipeline(self) -> None:
        # Simulate the loser of the PENDING -> EXECUTING CAS: a second
        # caller that arrives after a winner has acquired the proposal
        # must see ``transitioned is False`` and return True without
        # touching the pipeline.
        pipeline = StubWorkPipeline()
        worker = _FakeWorkerService()
        state, repo = await _seed(pipeline=pipeline, worker_service=worker)
        # Pre-acquire: simulate a concurrent winner already in EXECUTING.
        repo.items[sid("prop-a1")] = repo.items[sid("prop-a1")].model_copy(
            update={"status": ConversationalProposalStatus.EXECUTING}
        )
        handled = await try_conversational_intake_resume(
            state,
            sid("a1"),
            approved=True,
        )
        assert handled is True
        # Loser does not run the pipeline (winner owns it).
        assert pipeline.calls == []
        # Loser does not transition the state -- winner finishes it.
        assert (
            repo.items[sid("prop-a1")].status is ConversationalProposalStatus.EXECUTING
        )
