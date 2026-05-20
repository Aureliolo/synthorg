"""Unit tests for conversational clarify-and-propose domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import (
    Complexity,
    ConversationalProposalStatus,
    ConversationRole,
    ConversationStatus,
    Priority,
    TaskType,
)
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
    ProposeDecision,
    ProposedWork,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


class TestConversation:
    """Conversation model."""

    def _make(self, **overrides: object) -> Conversation:
        defaults: dict[str, object] = {
            "id": "conv-1",
            "created_by": "user-1",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
        defaults.update(overrides)
        return Conversation(**defaults)  # type: ignore[arg-type]

    def test_default_status_active(self) -> None:
        assert self._make().status is ConversationStatus.ACTIVE

    def test_frozen(self) -> None:
        conv = self._make()
        with pytest.raises(ValidationError):
            conv.status = ConversationStatus.CLOSED  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            self._make(unexpected="x")

    def test_blank_created_by_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(created_by="  ")

    def test_status_roundtrip(self) -> None:
        conv = self._make(status=ConversationStatus.PROPOSED)
        assert conv.status is ConversationStatus.PROPOSED


class TestConversationTurn:
    """ConversationTurn model."""

    def _make(self, **overrides: object) -> ConversationTurn:
        defaults: dict[str, object] = {
            "id": "turn-1",
            "conversation_id": "conv-1",
            "sequence": 0,
            "role": ConversationRole.USER,
            "content": "I need a new landing page",
            "created_at": _NOW,
        }
        defaults.update(overrides)
        return ConversationTurn(**defaults)  # type: ignore[arg-type]

    def test_roles(self) -> None:
        assert self._make(role=ConversationRole.ASSISTANT).role is (
            ConversationRole.ASSISTANT
        )

    def test_sequence_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            self._make(sequence=-1)

    def test_sequence_zero_allowed(self) -> None:
        assert self._make(sequence=0).sequence == 0

    def test_blank_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(content="   ")

    def test_frozen(self) -> None:
        turn = self._make()
        with pytest.raises(ValidationError):
            turn.content = "x"  # type: ignore[misc]


class TestProposedWork:
    """ProposedWork model."""

    def _make(self, **overrides: object) -> ProposedWork:
        defaults: dict[str, object] = {
            "title": "Build landing page",
            "raw_intent": "Create a marketing landing page for launch",
        }
        defaults.update(overrides)
        return ProposedWork(**defaults)  # type: ignore[arg-type]

    def test_defaults(self) -> None:
        pw = self._make()
        assert pw.project is None
        assert pw.priority is Priority.MEDIUM
        assert pw.task_type is TaskType.DEVELOPMENT
        assert pw.estimated_complexity is Complexity.MEDIUM
        assert pw.acceptance_criteria == ()

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(title=" ")

    def test_acceptance_criteria_tuple(self) -> None:
        pw = self._make(acceptance_criteria=("renders", "responsive"))
        assert pw.acceptance_criteria == ("renders", "responsive")

    def test_frozen(self) -> None:
        pw = self._make()
        with pytest.raises(ValidationError):
            pw.title = "x"  # type: ignore[misc]


class TestProposeDecision:
    """ProposeDecision XOR invariant."""

    def test_clarification_path(self) -> None:
        d = ProposeDecision(
            needs_clarification=True,
            clarifying_question="Which audience is the page for?",
        )
        assert d.needs_clarification
        assert d.proposals == ()

    def test_proposal_path(self) -> None:
        d = ProposeDecision(
            needs_clarification=False,
            proposals=(
                ProposedWork(
                    title="Build landing page",
                    raw_intent="Create the page",
                ),
            ),
        )
        assert not d.needs_clarification
        assert len(d.proposals) == 1

    def test_clarification_requires_question(self) -> None:
        with pytest.raises(ValidationError, match="clarifying_question"):
            ProposeDecision(needs_clarification=True)

    def test_clarification_forbids_proposals(self) -> None:
        with pytest.raises(ValidationError):
            ProposeDecision(
                needs_clarification=True,
                clarifying_question="What exactly?",
                proposals=(ProposedWork(title="x", raw_intent="y"),),
            )

    def test_proposal_path_requires_proposals(self) -> None:
        with pytest.raises(ValidationError, match="proposals"):
            ProposeDecision(needs_clarification=False)

    def test_proposal_path_forbids_question(self) -> None:
        with pytest.raises(ValidationError):
            ProposeDecision(
                needs_clarification=False,
                clarifying_question="should not be here",
                proposals=(ProposedWork(title="x", raw_intent="y"),),
            )

    def test_frozen(self) -> None:
        d = ProposeDecision(
            needs_clarification=True,
            clarifying_question="What?",
        )
        with pytest.raises(ValidationError):
            d.needs_clarification = False  # type: ignore[misc]


class TestConversationalProposal:
    """ConversationalProposal model."""

    def _make(self, **overrides: object) -> ConversationalProposal:
        defaults: dict[str, object] = {
            "id": "prop-1",
            "conversation_id": "conv-1",
            "approval_id": "appr-1",
            "work_item_json": '{"title": "x"}',
            "created_at": _NOW,
        }
        defaults.update(overrides)
        return ConversationalProposal(**defaults)  # type: ignore[arg-type]

    def test_default_status_pending(self) -> None:
        assert self._make().status is ConversationalProposalStatus.PENDING

    def test_status_roundtrip(self) -> None:
        prop = self._make(status=ConversationalProposalStatus.EXECUTED)
        assert prop.status is ConversationalProposalStatus.EXECUTED

    def test_blank_work_item_json_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._make(work_item_json="  ")

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            self._make(extra="x")

    def test_frozen(self) -> None:
        prop = self._make()
        with pytest.raises(ValidationError):
            prop.status = ConversationalProposalStatus.REJECTED  # type: ignore[misc]
