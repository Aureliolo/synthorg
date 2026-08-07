"""Tests for turn intent resolution and the decision it records.

Whether a message starts work or is answered as a question is the most
consequential routing decision the product makes. It was invisible from the
logs, so a live run could not tell "the classifier picked explain" apart from
"no classifier was wired", which is the exact ambiguity these tests close.
"""

from collections.abc import Mapping, Sequence

import pytest
import structlog.testing

from synthorg.api.controllers._turn_intent import resolve_turn_intent
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import ConversationKind
from synthorg.meta.chief_of_staff.intent_models import (
    IntentOutcome,
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.chief_of_staff.resume_service import ConversationalResumeService
from synthorg.meta.state import MetaStateSlice
from synthorg.observability.events.chief_of_staff import COS_TURN_INTENT_ROUTED
from tests._shared import mock_of


class _FixedClassifier:
    """Classifier returning one prepared outcome, whatever the turn says."""

    def __init__(self, outcome: IntentOutcome) -> None:
        self._outcome = outcome

    async def classify(self, history: tuple[ConversationTurn, ...]) -> IntentOutcome:
        """Return the prepared outcome.

        Args:
            history: The turns the caller would have classified.

        Returns:
            The outcome this classifier was built with.
        """
        assert history
        return self._outcome


def _app_state() -> AppState:
    return AppState(config=RootConfig(company_name="test"))


def _group_conversation(state: AppState) -> None:
    """Wire a resume service reporting one live GROUP thread."""

    async def _get_conversation(conversation_id: NotBlankStr) -> Conversation:
        assert conversation_id
        now = state.clock.now()
        return Conversation(
            created_by=NotBlankStr("operator"),
            created_at=now,
            updated_at=now,
            kind=ConversationKind.GROUP,
        )

    state.wire(
        MetaStateSlice,
        conversational_resume_service=mock_of[ConversationalResumeService](
            get_conversation=_get_conversation
        ),
    )


def _routing_events(
    logs: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return [entry for entry in logs if entry.get("event") == COS_TURN_INTENT_ROUTED]


@pytest.mark.unit
class TestRoutingDecisionIsLogged:
    """Every path that decides an intent records the decision it made."""

    async def test_the_no_classifier_default_says_so(self) -> None:
        # The path that logged nothing at all, which is why a live run could
        # not tell an unwired classifier from one that answered EXPLAIN.
        with structlog.testing.capture_logs() as logs:
            outcome = await resolve_turn_intent(
                _app_state(),
                body="build me a Tetris clone",
                override=None,
                conversation_id=None,
            )

        assert outcome.reason is IntentRoutingReason.NO_INTENT_CLASSIFIER
        events = _routing_events(logs)
        assert len(events) == 1
        assert events[0]["intent"] == TurnIntent.EXPLAIN.value
        assert events[0]["reason"] == IntentRoutingReason.NO_INTENT_CLASSIFIER.value
        assert events[0]["model"] is None

    async def test_an_override_logs_what_it_forced(self) -> None:
        with structlog.testing.capture_logs() as logs:
            outcome = await resolve_turn_intent(
                _app_state(),
                body="go",
                override=TurnIntent.ACT,
                conversation_id=None,
                named_targets=(NotBlankStr("Backend Developer"),),
            )

        assert outcome.intent is TurnIntent.ACT
        events = _routing_events(logs)
        assert len(events) == 1
        assert events[0]["reason"] == IntentRoutingReason.EXPLICIT_OVERRIDE.value
        # The count, not the names: a target is operator-authored text and the
        # log is not a place to reproduce it.
        assert events[0]["named_targets"] == 1

    async def test_a_fixed_group_thread_logs_its_short_circuit(self) -> None:
        state = _app_state()
        _group_conversation(state)

        with structlog.testing.capture_logs() as logs:
            outcome = await resolve_turn_intent(
                state,
                body="thanks",
                override=None,
                conversation_id=NotBlankStr("conv-1"),
            )

        assert outcome.intent is TurnIntent.GROUP_CONVENE
        events = _routing_events(logs)
        assert len(events) == 1
        assert events[0]["reason"] == IntentRoutingReason.CONVERSATION_KIND_FIXED.value

    async def test_a_classified_turn_names_the_model_that_decided_it(self) -> None:
        state = _app_state()
        state.wire(
            MetaStateSlice,
            turn_intent_classifier=_FixedClassifier(
                IntentOutcome(
                    intent=TurnIntent.ACT,
                    reason=IntentRoutingReason.CLASSIFIED,
                    confidence=0.91,
                    model=NotBlankStr("example-medium-001"),
                )
            ),
        )

        with structlog.testing.capture_logs() as logs:
            outcome = await resolve_turn_intent(
                state,
                body="build me a Tetris clone",
                override=None,
                conversation_id=None,
            )

        assert outcome.intent is TurnIntent.ACT
        events = _routing_events(logs)
        assert len(events) == 1
        # The model the dispatch actually resolved, not the one bound at build
        # time: diagnosing a misrouted turn means knowing which one answered,
        # and a live write can have moved it since the classifier was built.
        assert events[0]["model"] == "example-medium-001"
        assert events[0]["confidence"] == 0.91

    async def test_a_degraded_classification_logs_the_floor_it_missed(self) -> None:
        state = _app_state()
        state.wire(
            MetaStateSlice,
            turn_intent_classifier=_FixedClassifier(
                IntentOutcome(
                    intent=TurnIntent.EXPLAIN,
                    reason=IntentRoutingReason.ACT_FLOOR_NOT_MET,
                    confidence=0.62,
                    model=NotBlankStr("example-medium-001"),
                )
            ),
        )

        with structlog.testing.capture_logs() as logs:
            await resolve_turn_intent(
                state,
                body="maybe build something",
                override=None,
                conversation_id=None,
            )

        events = _routing_events(logs)
        assert len(events) == 1
        # Degrading to EXPLAIN and answering EXPLAIN outright are the same
        # response to the operator and different faults to the maintainer, so
        # the reason has to distinguish them.
        assert events[0]["reason"] == IntentRoutingReason.ACT_FLOOR_NOT_MET.value
        assert events[0]["intent"] == TurnIntent.EXPLAIN.value

    async def test_the_decision_is_logged_exactly_once_per_turn(self) -> None:
        # One turn, one decision. A second emission would double-count every
        # routing metric built on this event.
        state = _app_state()
        state.wire(
            MetaStateSlice,
            turn_intent_classifier=_FixedClassifier(
                IntentOutcome(
                    intent=TurnIntent.EXPLAIN,
                    reason=IntentRoutingReason.CLASSIFIED,
                    confidence=0.9,
                    model=NotBlankStr("example-medium-001"),
                )
            ),
        )

        with structlog.testing.capture_logs() as logs:
            await resolve_turn_intent(
                state,
                body="what is the team working on",
                override=None,
                conversation_id=None,
            )

        assert [entry["event"] for entry in logs].count(COS_TURN_INTENT_ROUTED) == 1
