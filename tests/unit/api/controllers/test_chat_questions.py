"""Tests for the parked-question surface on the unified conversation."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers import _approval_review_gate
from synthorg.api.controllers._chat_questions import DECLINE_REASON
from synthorg.approval.enums import (
    ApprovalRiskLevel,
    ApprovalSource,
    ApprovalStatus,
)
from synthorg.approval.questions import DECLINED_QUESTION_NOTE
from synthorg.approval.resume_annotations import (
    DEFAULT_RESUME_ANNOTATIONS,
    ResumeAnnotations,
    ResumeReasonProvenance,
)
from synthorg.core.approval import ApprovalItem
from synthorg.core.evidence import EvidencePackage, RecommendedAction
from synthorg.core.plan import PlanOption
from synthorg.core.types import NotBlankStr
from synthorg.workers.execution_service import WorkerExecutionService
from tests._shared import JsonDict, LoopAsyncClient, as_uuid, mock_of
from tests.unit.api.conftest import make_approval, make_auth_headers

_BASE = "/api/v1/meta/chat/questions"
_DECIDE_HEADERS = make_auth_headers("ceo")
_READ_HEADERS = make_auth_headers("observer")


def _idem(headers: Mapping[str, str]) -> dict[str, str]:
    """Merge a fresh required Idempotency-Key into decision request headers.

    Returns:
        A new headers dict carrying a unique ``Idempotency-Key``.
    """
    return {**headers, "Idempotency-Key": str(uuid4())}


class _Resumes:
    """The resume calls one test observed, in order."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.calls.append(kwargs)

    def __len__(self) -> int:
        return len(self.calls)

    def __getitem__(self, index: int) -> dict[str, object]:
        return self.calls[index]


@pytest.fixture
def resumes(monkeypatch: pytest.MonkeyPatch) -> _Resumes:
    """Stand in for the agent runtime so a parked decision can complete.

    Without a wired runtime the shared test app raises
    ``AgentRuntimeNotConfiguredError`` and rolls the decision back, which is
    the correct production behaviour but leaves nothing to assert about the
    answer that reaches the agent.

    An autospec'd ``WorkerExecutionService`` rather than a one-method class:
    the protocol has three methods, so a hand-rolled stand-in implementing one
    is not an instance of it, and a signature drifting out from under this test
    would go unnoticed until the isinstance check somewhere else failed.
    """
    observed = _Resumes()
    service = mock_of[WorkerExecutionService](
        dispatch_resume=AsyncMock(side_effect=observed.record)
    )
    assert isinstance(service, WorkerExecutionService)
    monkeypatch.setattr(
        _approval_review_gate,
        "worker_execution_service_of",
        lambda _app_state: service,
    )
    return observed


def _question(
    *,
    approval_id: str,
    question: str = "Which database backend should I target?",
    reversibility: str | None = "reversible",
    created_at: datetime | None = None,
    task_id: str | None = None,
) -> ApprovalItem:
    """Build a parked clarification exactly as the tool creates one."""
    metadata = {"source": "request_clarification", "clarification": "true"}
    if reversibility is not None:
        metadata["reversibility"] = reversibility
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type="clarify:question",
        title="Clarification requested",
        description=question,
        requested_by="agent-dev",
        risk_level=ApprovalRiskLevel.LOW,
        source=ApprovalSource.PARKED_CONTEXT,
        created_at=created_at or datetime.now(UTC),
        task_id=task_id,
        metadata=metadata,
    )


def _decision(*, approval_id: str) -> ApprovalItem:
    """Build a parked project decision carrying two structured options."""
    now = datetime.now(UTC)
    options = (
        PlanOption(
            id=NotBlankStr("postgres"),
            title=NotBlankStr("PostgreSQL"),
            summary=NotBlankStr("Concurrent writers, an extra service to run."),
            recommended=True,
        ),
        PlanOption(
            id=NotBlankStr("sqlite"),
            title=NotBlankStr("SQLite"),
            summary=NotBlankStr("Zero ops, single writer."),
        ),
    )
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type="decision:project",
        title="Project decision requested",
        description="Which database should the project use?",
        requested_by="agent-lead",
        risk_level=ApprovalRiskLevel.LOW,
        source=ApprovalSource.PARKED_CONTEXT,
        created_at=now,
        evidence_package=EvidencePackage(
            id=NotBlankStr(approval_id),
            title=NotBlankStr("Which database?"),
            narrative=NotBlankStr("Which database should the project use?"),
            recommended_actions=(
                RecommendedAction(
                    action_type=NotBlankStr("approve"),
                    label=NotBlankStr("Approve with the selected option"),
                    description=NotBlankStr("Proceed with the option you pick."),
                ),
            ),
            options=options,
            source_agent_id=NotBlankStr("agent-lead"),
            risk_level=ApprovalRiskLevel.LOW,
            created_at=now,
        ),
        metadata={
            "source": "request_project_decision",
            "clarification": "true",
            "decision": "true",
            "reversibility": "hard_to_reverse",
        },
    )


@pytest.mark.unit
class TestListQuestions:
    async def test_empty(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_lists_only_pending_questions(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await approval_store.add(_question(approval_id="q-open"))
        await approval_store.add(_decision(approval_id="q-decision"))
        # Not a question: a plain approval must never surface here.
        await approval_store.add(make_approval(approval_id="q-other"))
        answered = _question(approval_id="q-done").model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": datetime.now(UTC),
                "decided_by": "test-ceo",
                "decision_reason": "Use Postgres.",
            }
        )
        await approval_store.add(answered)

        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200
        ids = {row["approval_id"] for row in resp.json()["data"]}
        assert ids == {str(as_uuid("q-open")), str(as_uuid("q-decision"))}

    async def test_hard_to_reverse_sorts_first(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        older = datetime.now(UTC) - timedelta(hours=1)
        await approval_store.add(
            _question(
                approval_id="q-easy", reversibility="reversible", created_at=older
            )
        )
        await approval_store.add(
            _question(approval_id="q-hard", reversibility="hard_to_reverse")
        )
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        rows: list[JsonDict] = resp.json()["data"]
        assert rows[0]["approval_id"] == str(as_uuid("q-hard"))

    async def test_projects_options_from_the_evidence_package(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await approval_store.add(_decision(approval_id="q-decision"))
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        row = resp.json()["data"][0]
        assert row["is_decision"] is True
        assert [o["id"] for o in row["options"]] == ["postgres", "sqlite"]
        assert row["options"][0]["recommended"] is True

    async def test_clarification_carries_no_options(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await approval_store.add(_question(approval_id="q-open"))
        row = (await async_test_client.get(_BASE, headers=_READ_HEADERS)).json()[
            "data"
        ][0]
        assert row["is_decision"] is False
        assert row["options"] == []

    async def test_unclassified_reversibility_stays_null(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        # A question parked before the tools required it declared nothing, and
        # inventing a value would fabricate the very signal the field carries.
        await approval_store.add(_question(approval_id="q-old", reversibility=None))
        row = (await async_test_client.get(_BASE, headers=_READ_HEADERS)).json()[
            "data"
        ][0]
        assert row["reversibility"] is None


@pytest.mark.unit
@pytest.mark.usefixtures("resumes")
class TestAnswerQuestion:
    async def test_answer_resumes_the_run_with_the_answer(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        resumes: _Resumes,
    ) -> None:
        await approval_store.add(_question(approval_id="q-open"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-open')}/answer",
            json={"answer": "Use Postgres, not SQLite."},
            headers=_idem(_DECIDE_HEADERS),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "approved"
        assert body["recorded_answer"] == "Use Postgres, not SQLite."

        item = await approval_store.get(str(as_uuid("q-open")))
        assert item is not None
        assert item.status is ApprovalStatus.APPROVED
        assert item.decision_reason == "Use Postgres, not SQLite."

        # The answer is what the parked run resumes with, presented as the
        # operator's own words because that is what it is.
        assert resumes.calls == [
            {
                "approval_id": str(as_uuid("q-open")),
                "approved": True,
                "decided_by": "test-ceo",
                "decision_reason": "Use Postgres, not SQLite.",
                "annotations": DEFAULT_RESUME_ANNOTATIONS,
            }
        ]

    async def test_blank_answer_is_rejected_and_leaves_it_pending(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await approval_store.add(_question(approval_id="q-blank"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-blank')}/answer",
            json={"answer": "   "},
            headers=_idem(_DECIDE_HEADERS),
        )
        assert resp.status_code == 400
        item = await approval_store.get(str(as_uuid("q-blank")))
        assert item is not None
        assert item.status is ApprovalStatus.PENDING

    async def test_decision_resolves_the_chosen_option_writeup(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        resumes: _Resumes,
    ) -> None:
        await approval_store.add(_decision(approval_id="q-pick"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-pick')}/answer",
            json={"answer": "SQLite", "chosen_option_id": "sqlite"},
            headers=_idem(_DECIDE_HEADERS),
        )
        assert resp.status_code == 200
        item = await approval_store.get(str(as_uuid("q-pick")))
        assert item is not None
        # The option's writeup, not the typed text, is what the agent resumes
        # with, exactly as on the approvals door.
        assert "SQLite" in (item.decision_reason or "")
        assert "Zero ops" in (item.decision_reason or "")
        # And it resumes labelled as the agent's own prose, because that is
        # who wrote it: the operator supplied the pick, not the wording.
        annotations = resumes[0]["annotations"]
        assert isinstance(annotations, ResumeAnnotations)
        assert annotations.reason_provenance is ResumeReasonProvenance.AGENT_OPTION

    async def test_unknown_id_is_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/{uuid4()}/answer",
            json={"answer": "Anything."},
            headers=_idem(_DECIDE_HEADERS),
        )
        assert resp.status_code == 404

    async def test_non_question_is_not_found_and_untouched(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        # A uniform 404 so an arbitrary approval id cannot be probed through
        # this narrow door, and the precondition runs before any write.
        await approval_store.add(make_approval(approval_id="q-plain"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-plain')}/answer",
            json={"answer": "Anything."},
            headers=_idem(_DECIDE_HEADERS),
        )
        assert resp.status_code == 404
        item = await approval_store.get(str(as_uuid("q-plain")))
        assert item is not None
        assert item.status is ApprovalStatus.PENDING

    async def test_scope_is_refused_before_the_pending_check(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        # An ALREADY-DECIDED non-question is the only case that tells the two
        # checks apart: scope-first answers 404, pending-first would answer
        # 409 and thereby confirm the id exists.
        decided = make_approval(approval_id="q-decided-plain").model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": datetime.now(UTC),
                "decided_by": "test-ceo",
                "decision_reason": "shipped",
            }
        )
        await approval_store.add(decided)

        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-decided-plain')}/answer",
            json={"answer": "Anything."},
            headers=_idem(_DECIDE_HEADERS),
        )

        assert resp.status_code == 404

    async def test_one_key_across_two_questions_decides_both(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        resumes: _Resumes,
    ) -> None:
        # The dedup key binds the approval id, so a client reusing one key
        # across two questions must not have the second replay the first's
        # cached response and silently leave that agent parked forever.
        await approval_store.add(_question(approval_id="q-key-a"))
        await approval_store.add(_question(approval_id="q-key-b"))
        shared = {**_DECIDE_HEADERS, "Idempotency-Key": str(uuid4())}

        first = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-key-a')}/answer",
            json={"answer": "Answer A."},
            headers=shared,
        )
        second = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-key-b')}/answer",
            json={"answer": "Answer B."},
            headers=shared,
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["data"]["approval_id"] == str(as_uuid("q-key-b"))
        assert len(resumes) == 2

    async def test_answering_twice_conflicts_and_resumes_once(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        resumes: _Resumes,
    ) -> None:
        await approval_store.add(_question(approval_id="q-twice"))
        first = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-twice')}/answer",
            json={"answer": "First answer."},
            headers=_idem(_DECIDE_HEADERS),
        )
        assert first.status_code == 200
        second = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-twice')}/answer",
            json={"answer": "Second answer."},
            headers=_idem(_DECIDE_HEADERS),
        )
        assert second.status_code == 409
        item = await approval_store.get(str(as_uuid("q-twice")))
        assert item is not None
        assert item.decision_reason == "First answer."
        assert len(resumes) == 1

    @pytest.mark.parametrize("route", ["answer", "decline"])
    async def test_idempotency_key_is_required(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        route: str,
    ) -> None:
        # Without it a 5xx-driven client retry would double-fire the resume.
        await approval_store.add(_question(approval_id=f"q-nokey-{route}"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid(f'q-nokey-{route}')}/{route}",
            json={"answer": "Anything."} if route == "answer" else None,
            headers=_DECIDE_HEADERS,
        )
        assert resp.status_code == 400


@pytest.mark.unit
@pytest.mark.usefixtures("resumes")
class TestDeclineQuestion:
    async def test_decline_resumes_with_the_fixed_reason(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        resumes: _Resumes,
    ) -> None:
        await approval_store.add(_question(approval_id="q-decline"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-decline')}/decline",
            headers=_idem(_DECIDE_HEADERS),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "rejected"
        assert body["recorded_answer"] == DECLINE_REASON
        # Server-owned text, never operator input: the route takes no body.
        assert resumes[0]["decision_reason"] is DECLINE_REASON
        assert resumes[0]["approved"] is False
        # The reason is fenced by contract, so the instruction the agent is
        # meant to ACT on rides the separate trusted channel.
        annotations = resumes[0]["annotations"]
        assert isinstance(annotations, ResumeAnnotations)
        assert annotations.system_note == DECLINED_QUESTION_NOTE

    async def test_decline_needs_no_chosen_option(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        # The only option-free exit from a project decision.
        await approval_store.add(_decision(approval_id="q-decline-decision"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-decline-decision')}/decline",
            headers=_idem(_DECIDE_HEADERS),
        )
        assert resp.status_code == 200


@pytest.mark.unit
@pytest.mark.usefixtures("resumes")
class TestAuthorisation:
    async def test_observer_may_read(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)
        assert resp.status_code == 200

    @pytest.mark.parametrize("role", ["observer", "pair_programmer"])
    async def test_non_decider_may_not_answer(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
        role: str,
    ) -> None:
        # Same write as the approvals door, so the same guard: reaching this
        # surface must not confer authority the Approvals page refuses.
        await approval_store.add(_question(approval_id=f"q-{role}"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid(f'q-{role}')}/answer",
            json={"answer": "Anything."},
            headers=_idem(make_auth_headers(role)),
        )
        assert resp.status_code == 403

    async def test_board_member_may_answer(
        self,
        async_test_client: LoopAsyncClient,
        approval_store: ApprovalStore,
    ) -> None:
        await approval_store.add(_question(approval_id="q-board"))
        resp = await async_test_client.post(
            f"{_BASE}/{as_uuid('q-board')}/answer",
            json={"answer": "Anything."},
            headers=_idem(make_auth_headers("board_member")),
        )
        assert resp.status_code == 200
