"""Unit tests for conversational clarify-and-propose domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.communication.conversation.enums import (
    ConversationRole,
    ConversationStatus,
)
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationTurn,
    ProposeDecision,
    ProposedSteering,
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


class TestProposeDecision:
    """ProposeDecision XOR invariant."""

    def test_clarification_path(self) -> None:
        d = ProposeDecision(
            needs_clarification=True,
            clarifying_question="Which audience is the page for?",
        )
        assert d.needs_clarification
        assert d.steering == ()

    def test_no_work_brief_can_be_expressed(self) -> None:
        """Starting work is not reachable from this surface at all.

        The charter interview and the operator's approval of what it drafts
        are the one way an initiative begins, so the decision this turn
        produces has no field that could ask for one.
        """
        assert "work" not in ProposeDecision.model_fields

        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ProposeDecision(
                needs_clarification=False,
                work={"title": "x", "raw_intent": "y"},  # type: ignore[call-arg]
            )

    def test_clarification_requires_question(self) -> None:
        with pytest.raises(ValidationError, match="clarifying_question"):
            ProposeDecision(needs_clarification=True)

    def test_proposal_path_requires_steering(self) -> None:
        with pytest.raises(ValidationError, match="steering must be present"):
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
        assert len(d.steering) == 1
        assert d.steering[0].kind is InterventionKind.REDIRECT

    def test_clarification_forbids_steering(self) -> None:
        with pytest.raises(ValidationError):
            ProposeDecision(
                needs_clarification=True,
                clarifying_question="What exactly?",
                steering=(
                    ProposedSteering(kind=InterventionKind.HINT, text="advisory"),
                ),
            )

    def test_steering_path_forbids_question(self) -> None:
        with pytest.raises(ValidationError):
            ProposeDecision(
                needs_clarification=False,
                clarifying_question="should not be here",
                steering=(
                    ProposedSteering(kind=InterventionKind.HINT, text="advisory"),
                ),
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
        assert len(result.steering) == 1

    def test_clarification_forbids_steering(self) -> None:
        with pytest.raises(ValidationError):
            ProposeResult(
                conversation_id="conv-1",
                status="needs_clarification",
                clarifying_question="which project?",
                steering=(self._summary(),),
            )
