"""Unit tests for conversational clarify-and-propose domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.communication.conversation.enums import (
    ConversationalProposalStatus,
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.task_enums import Complexity, Priority, TaskType
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
    ProposeDecision,
    ProposedSteering,
    ProposedWork,
    ProposeResult,
    SteeringProposalSummary,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


class TestConversation:
    """Conversation model."""

    def _make(self, **overrides: object) -> Conversation:
        defaults: dict[str, object] = {
            "id": as_uuid("conv-1"),
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
            "id": as_uuid("turn-1"),
            "conversation_id": sid("conv-1"),
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

    def test_proposal_path_requires_one_of_proposals_or_steering(self) -> None:
        with pytest.raises(ValidationError, match="proposals or steering"):
            ProposeDecision(needs_clarification=False)

    def test_steering_only_path(self) -> None:
        d = ProposeDecision(
            needs_clarification=False,
            steering=(
                ProposedSteering(
                    project="checkout",
                    kind=InterventionKind.REDIRECT,
                    text="use Postgres not Mongo",
                ),
            ),
        )
        assert not d.needs_clarification
        assert d.proposals == ()
        assert len(d.steering) == 1
        assert d.steering[0].kind is InterventionKind.REDIRECT

    def test_proposals_and_steering_together(self) -> None:
        d = ProposeDecision(
            needs_clarification=False,
            proposals=(ProposedWork(title="x", raw_intent="y"),),
            steering=(
                ProposedSteering(kind=InterventionKind.HINT, text="prefer the util"),
            ),
        )
        assert len(d.proposals) == 1
        assert len(d.steering) == 1

    def test_clarification_forbids_steering(self) -> None:
        with pytest.raises(ValidationError):
            ProposeDecision(
                needs_clarification=True,
                clarifying_question="What exactly?",
                steering=(
                    ProposedSteering(kind=InterventionKind.HINT, text="advisory"),
                ),
            )

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


class TestProposedSteering:
    """ProposedSteering steerable-kind invariant."""

    def test_hint_and_redirect_allowed(self) -> None:
        assert (
            ProposedSteering(kind=InterventionKind.HINT, text="advisory").kind
            is InterventionKind.HINT
        )
        assert (
            ProposedSteering(kind=InterventionKind.REDIRECT, text="pivot").kind
            is InterventionKind.REDIRECT
        )

    def test_project_optional(self) -> None:
        assert ProposedSteering(kind=InterventionKind.HINT, text="x").project is None

    @pytest.mark.parametrize("kind", [InterventionKind.PAUSE, InterventionKind.KILL])
    def test_non_steerable_kind_rejected(self, kind: InterventionKind) -> None:
        with pytest.raises(ValidationError, match="not a steerable directive kind"):
            ProposedSteering(kind=kind, text="halt it")

    def test_blank_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ProposedSteering(kind=InterventionKind.HINT, text="  ")

    def test_frozen(self) -> None:
        steer = ProposedSteering(kind=InterventionKind.HINT, text="x")
        with pytest.raises(ValidationError):
            steer.text = "y"  # type: ignore[misc]


class TestProposeResultSteering:
    """ProposeResult steering summaries respect the status invariant."""

    def _summary(self) -> SteeringProposalSummary:
        return SteeringProposalSummary(
            approval_id="appr-steer",
            kind=InterventionKind.REDIRECT,
            text="use Postgres not Mongo",
            project="checkout",
        )

    def test_proposed_with_only_steering(self) -> None:
        result = ProposeResult(
            conversation_id="conv-1",
            status="proposed",
            steering=(self._summary(),),
        )
        assert result.proposals == ()
        assert len(result.steering) == 1

    def test_clarification_forbids_steering(self) -> None:
        with pytest.raises(ValidationError):
            ProposeResult(
                conversation_id="conv-1",
                status="needs_clarification",
                clarifying_question="which project?",
                steering=(self._summary(),),
            )


class TestConversationalProposal:
    """ConversationalProposal model."""

    def _make(self, **overrides: object) -> ConversationalProposal:
        defaults: dict[str, object] = {
            "id": as_uuid("prop-1"),
            "conversation_id": sid("conv-1"),
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
