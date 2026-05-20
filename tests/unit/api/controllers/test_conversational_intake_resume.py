"""Unit tests for the conversational-intake approval dispatch branch."""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_review_gate import (
    try_conversational_intake_resume,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalSource,
    ApprovalStatus,
    ConversationalProposalStatus,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.meta.chief_of_staff.models import ConversationalProposal
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
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
    def __init__(self) -> None:
        self.items: dict[str, ConversationalProposal] = {}

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

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationalProposalStatus,
        to_state: ConversationalProposalStatus,
        **updates: object,
    ) -> bool:
        cur = self.items.get(entity_id)
        if cur is None or cur.status is not from_state:
            return False
        self.items[entity_id] = cur.model_copy(update={"status": to_state})
        return True


class _FakePipeline:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[WorkItem] = []

    async def run(self, work_item: WorkItem) -> object:
        self.calls.append(work_item)
        if self.error is not None:
            raise self.error
        return object()


class _FakeAppState:
    def __init__(
        self,
        *,
        approval_store: ApprovalStore,
        proposal_repo: _FakeProposalRepo | None,
        pipeline: _FakePipeline | None,
    ) -> None:
        self.approval_store = approval_store
        self._proposal_repo = proposal_repo
        self._pipeline = pipeline

    @property
    def has_conversational_proposal_repo(self) -> bool:
        return self._proposal_repo is not None

    @property
    def conversational_proposal_repo(self) -> _FakeProposalRepo:
        assert self._proposal_repo is not None
        return self._proposal_repo

    @property
    def has_work_pipeline(self) -> bool:
        return self._pipeline is not None

    @property
    def work_pipeline(self) -> _FakePipeline:
        assert self._pipeline is not None
        return self._pipeline


def _approval(
    approval_id: str,
    *,
    source: ApprovalSource = ApprovalSource.CONVERSATIONAL_INTAKE,
) -> ApprovalItem:
    return ApprovalItem(
        id=NotBlankStr(approval_id),
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
        id=NotBlankStr(f"prop-{approval_id}"),
        conversation_id=NotBlankStr("conv-1"),
        approval_id=NotBlankStr(approval_id),
        work_item_json=NotBlankStr(_work_item_json()),
        status=ConversationalProposalStatus.PENDING,
        created_at=_NOW,
    )


async def _seed(
    *,
    source: ApprovalSource = ApprovalSource.CONVERSATIONAL_INTAKE,
    with_proposal: bool = True,
    pipeline: _FakePipeline | None = None,
) -> tuple[_FakeAppState, _FakeProposalRepo]:
    store = ApprovalStore()
    await store.add(_approval("a1", source=source))
    repo = _FakeProposalRepo()
    if with_proposal:
        prop = _proposal("a1")
        repo.items[prop.id] = prop
    state = _FakeAppState(
        approval_store=store,
        proposal_repo=repo,
        pipeline=pipeline if pipeline is not None else _FakePipeline(),
    )
    return state, repo


class TestConversationalIntakeResume:
    async def test_non_conversational_source_is_inert(self) -> None:
        state, _ = await _seed(source=ApprovalSource.REVIEW_GATE)
        handled = await try_conversational_intake_resume(
            state,  # type: ignore[arg-type]
            "a1",
            approved=True,
        )
        assert handled is False

    async def test_approve_runs_pipeline_and_marks_executed(self) -> None:
        pipeline = _FakePipeline()
        state, repo = await _seed(pipeline=pipeline)
        handled = await try_conversational_intake_resume(
            state,  # type: ignore[arg-type]
            "a1",
            approved=True,
        )
        assert handled is True
        assert len(pipeline.calls) == 1
        assert pipeline.calls[0].source is WorkSource.CONVERSATIONAL
        assert repo.items["prop-a1"].status is ConversationalProposalStatus.EXECUTED

    async def test_reject_skips_pipeline_and_marks_rejected(self) -> None:
        pipeline = _FakePipeline()
        state, repo = await _seed(pipeline=pipeline)
        handled = await try_conversational_intake_resume(
            state,  # type: ignore[arg-type]
            "a1",
            approved=False,
        )
        assert handled is True
        assert pipeline.calls == []
        assert repo.items["prop-a1"].status is ConversationalProposalStatus.REJECTED

    async def test_missing_proposal_is_owned_but_noop(self) -> None:
        state, _ = await _seed(with_proposal=False)
        handled = await try_conversational_intake_resume(
            state,  # type: ignore[arg-type]
            "a1",
            approved=True,
        )
        assert handled is True

    async def test_pipeline_unavailable_raises(self) -> None:
        store = ApprovalStore()
        await store.add(_approval("a1"))
        repo = _FakeProposalRepo()
        prop = _proposal("a1")
        repo.items[prop.id] = prop
        state = _FakeAppState(
            approval_store=store,
            proposal_repo=repo,
            pipeline=None,
        )
        with pytest.raises(ServiceUnavailableError):
            await try_conversational_intake_resume(
                state,  # type: ignore[arg-type]
                "a1",
                approved=True,
            )

    async def test_missing_proposal_repo_raises(self) -> None:
        # Hard misconfiguration: a conversational-intake approval lands
        # on a deployment where the proposal repo was never wired. The
        # gate cannot drive the decision either way, so it must raise
        # rather than silently mark the approval handled.
        store = ApprovalStore()
        await store.add(_approval("a1"))
        state = _FakeAppState(
            approval_store=store,
            proposal_repo=None,
            pipeline=_FakePipeline(),
        )
        with pytest.raises(ServiceUnavailableError):
            await try_conversational_intake_resume(
                state,  # type: ignore[arg-type]
                "a1",
                approved=True,
            )

    async def test_pipeline_failure_reverts_executing_to_pending(self) -> None:
        # On pipeline failure the proposal must revert from EXECUTING
        # back to PENDING so a future approval-decision retry can run;
        # leaving it stuck in EXECUTING would silently lock the row.
        pipeline = _FakePipeline(error=RuntimeError("boom"))
        state, repo = await _seed(pipeline=pipeline)
        handled = await try_conversational_intake_resume(
            state,  # type: ignore[arg-type]
            "a1",
            approved=True,
        )
        assert handled is True
        assert repo.items["prop-a1"].status is ConversationalProposalStatus.PENDING

    async def test_unreadable_approval_item_raises(self) -> None:
        # The approval-store reread raised (or returned ``None``) on
        # this conversational approval. Flow 0 owns the source the
        # moment it reads it, so a missing read cannot fall through
        # to other resume flows without stranding a decided approval.
        store = ApprovalStore()
        # Deliberately do NOT add the approval to the store, so
        # ``_reread_approval_item`` returns ``None``.
        repo = _FakeProposalRepo()
        state = _FakeAppState(
            approval_store=store,
            proposal_repo=repo,
            pipeline=_FakePipeline(),
        )
        with pytest.raises(ServiceUnavailableError):
            await try_conversational_intake_resume(
                state,  # type: ignore[arg-type]
                "a1",
                approved=True,
            )

    async def test_concurrent_acquire_only_one_runs_pipeline(self) -> None:
        # Simulate the loser of the PENDING -> EXECUTING CAS: a second
        # caller that arrives after a winner has acquired the proposal
        # must see ``transitioned is False`` and return True without
        # touching the pipeline.
        pipeline = _FakePipeline()
        state, repo = await _seed(pipeline=pipeline)
        # Pre-acquire: simulate a concurrent winner already in EXECUTING.
        repo.items["prop-a1"] = repo.items["prop-a1"].model_copy(
            update={"status": ConversationalProposalStatus.EXECUTING}
        )
        handled = await try_conversational_intake_resume(
            state,  # type: ignore[arg-type]
            "a1",
            approved=True,
        )
        assert handled is True
        # Loser does not run the pipeline (winner owns it).
        assert pipeline.calls == []
        # Loser does not transition the state -- winner finishes it.
        assert repo.items["prop-a1"].status is ConversationalProposalStatus.EXECUTING
