"""Unit tests for deep-interview project-charter domain models."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.core.enums import CharterStatus
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    CharterDraft,
    InterviewDecision,
    InterviewTurnResult,
    ProjectCharter,
    ScopeBoundaries,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _envelope(**overrides: object) -> BudgetEnvelope:
    defaults: dict[str, object] = {"amount": 1000.0, "currency": "USD"}
    defaults.update(overrides)
    return BudgetEnvelope(**defaults)  # type: ignore[arg-type]


class TestBudgetEnvelope:
    """BudgetEnvelope validation."""

    def test_amount_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _envelope(amount=0.0)

    def test_rejects_unknown_currency(self) -> None:
        with pytest.raises(ValidationError):
            _envelope(currency="ZZZ")

    def test_optional_deadline_and_horizon_default_none(self) -> None:
        env = _envelope()
        assert env.deadline is None
        assert env.time_horizon is None

    def test_frozen(self) -> None:
        env = _envelope()
        with pytest.raises(ValidationError):
            env.amount = 5.0  # type: ignore[misc]


class TestCharterDraft:
    """CharterDraft project-binding XOR."""

    def _make(self, **overrides: object) -> CharterDraft:
        defaults: dict[str, object] = {
            "title": "Better memory layer",
            "brief": "Build an alternative to the incumbent memory tool.",
            "envelope": _envelope(),
            "proposed_project_name": "memory-layer",
        }
        defaults.update(overrides)
        return CharterDraft(**defaults)  # type: ignore[arg-type]

    def test_proposed_project_path_valid(self) -> None:
        draft = self._make()
        assert draft.project_id is None
        assert draft.proposed_project_name == "memory-layer"

    def test_existing_project_path_valid(self) -> None:
        draft = self._make(proposed_project_name=None, project_id="proj-1")
        assert draft.project_id == "proj-1"

    def test_both_bindings_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(project_id="proj-1")

    def test_neither_binding_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(proposed_project_name=None)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            self._make(unexpected="x")


class TestInterviewDecision:
    """InterviewDecision elicit-XOR-draft invariant."""

    def _draft(self) -> CharterDraft:
        return CharterDraft(
            title="t",
            brief="b",
            envelope=_envelope(),
            proposed_project_name="p",
        )

    def test_needs_more_requires_question(self) -> None:
        with pytest.raises(ValidationError):
            InterviewDecision(needs_more=True)

    def test_needs_more_forbids_draft(self) -> None:
        with pytest.raises(ValidationError):
            InterviewDecision(
                needs_more=True,
                next_question="What is the budget?",
                draft=self._draft(),
            )

    def test_drafted_requires_draft(self) -> None:
        with pytest.raises(ValidationError):
            InterviewDecision(needs_more=False)

    def test_drafted_forbids_question(self) -> None:
        with pytest.raises(ValidationError):
            InterviewDecision(
                needs_more=False,
                next_question="leftover",
                draft=self._draft(),
            )

    def test_valid_clarify(self) -> None:
        decision = InterviewDecision(
            needs_more=True, next_question="What outcome matters most?"
        )
        assert decision.next_question == "What outcome matters most?"

    def test_valid_draft(self) -> None:
        decision = InterviewDecision(needs_more=False, draft=self._draft())
        assert decision.draft is not None

    @given(needs_more=st.booleans())
    def test_xor_property(self, needs_more: bool) -> None:
        kwargs: dict[str, object] = {"needs_more": needs_more}
        if needs_more:
            kwargs["next_question"] = "q?"
        else:
            kwargs["draft"] = self._draft()
        decision = InterviewDecision(**kwargs)  # type: ignore[arg-type]
        assert (decision.next_question is None) == (not needs_more)
        assert (decision.draft is None) == needs_more


class TestProjectCharter:
    """ProjectCharter lifecycle and approval-coupling invariants."""

    def _make(self, **overrides: object) -> ProjectCharter:
        defaults: dict[str, object] = {
            "id": "charter-1",
            "conversation_id": "conv-1",
            "created_by": "user-1",
            "title": "Better memory layer",
            "brief": "Build an alternative to the incumbent memory tool.",
            "success_criteria": ("Recall beats baseline by 10%",),
            "scope": ScopeBoundaries(in_scope=("retrieval",)),
            "envelope": _envelope(),
            "proposed_project_name": "memory-layer",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
        defaults.update(overrides)
        return ProjectCharter(**defaults)  # type: ignore[arg-type]

    def test_default_status_drafted_version_one(self) -> None:
        charter = self._make()
        assert charter.status is CharterStatus.DRAFTED
        assert charter.version == 1

    def test_drafted_forbids_approval_provenance(self) -> None:
        with pytest.raises(ValidationError):
            self._make(approved_by="user-1")

    def test_approved_requires_full_provenance(self) -> None:
        with pytest.raises(ValidationError):
            self._make(status=CharterStatus.APPROVED, approved_by="user-1")

    def test_approved_with_full_provenance_valid(self) -> None:
        charter = self._make(
            status=CharterStatus.APPROVED,
            approved_at=_NOW,
            approved_by="user-1",
            forecast_id=uuid4(),
            correlation_id="conv-1",
            task_id="task-1",
        )
        assert charter.status is CharterStatus.APPROVED

    def test_cancelled_forbids_provenance(self) -> None:
        with pytest.raises(ValidationError):
            self._make(status=CharterStatus.CANCELLED, task_id="task-1")

    def test_project_binding_xor_enforced(self) -> None:
        with pytest.raises(ValidationError):
            self._make(project_id="proj-1")  # both bindings set


class TestInterviewTurnResult:
    """InterviewTurnResult branch invariants."""

    def _charter(self) -> ProjectCharter:
        return ProjectCharter(
            id="charter-1",
            conversation_id="conv-1",
            created_by="user-1",
            title="t",
            brief="b",
            envelope=_envelope(),
            proposed_project_name="p",
            created_at=_NOW,
            updated_at=_NOW,
        )

    def test_needs_more_requires_question(self) -> None:
        with pytest.raises(ValidationError):
            InterviewTurnResult(conversation_id="conv-1", status="needs_more")

    def test_drafted_requires_charter(self) -> None:
        with pytest.raises(ValidationError):
            InterviewTurnResult(conversation_id="conv-1", status="drafted")

    def test_valid_needs_more(self) -> None:
        result = InterviewTurnResult(
            conversation_id="conv-1",
            status="needs_more",
            next_question="What is the deadline?",
        )
        assert result.charter is None

    def test_valid_drafted(self) -> None:
        result = InterviewTurnResult(
            conversation_id="conv-1",
            status="drafted",
            charter=self._charter(),
        )
        assert result.next_question is None
