"""Unit tests for the cockpit steering directive."""

import pytest
from tests._shared import FakeClock

from synthorg.communication.event_stream.interrupt import InterruptStore, InterruptType
from synthorg.core.enums import InterventionKind
from synthorg.engine.intervention import (
    SafeDefaultSteeringDirective,
    build_steering_directive,
)

pytestmark = pytest.mark.unit


class TestSafeDefaultSteeringDirective:
    async def test_hint_queues_info_request_interrupt(self) -> None:
        store = InterruptStore()
        directive = SafeDefaultSteeringDirective(store, clock=FakeClock())

        outcome = await directive.steer(
            kind=InterventionKind.HINT,
            execution_id="exec-1",
            agent_id="agent-1",
            details={"text": "use Postgres not Mongo"},
        )

        assert outcome.applied is True
        assert outcome.kind is InterventionKind.HINT
        assert outcome.artifact_id is not None
        pending = await store.get(outcome.artifact_id)
        assert pending is not None
        assert pending.type is InterruptType.INFO_REQUEST
        # The persisted question is the wrapped form so the downstream
        # agent reads it as untrusted content; the operator's raw text
        # is still recoverable inside the safety envelope.
        question = pending.question
        assert question is not None
        assert "use Postgres not Mongo" in question
        assert question.startswith("<task-data")

    async def test_redirect_also_queues_interrupt(self) -> None:
        store = InterruptStore()
        directive = SafeDefaultSteeringDirective(store, clock=FakeClock())

        outcome = await directive.steer(
            kind=InterventionKind.REDIRECT,
            execution_id="exec-1",
            agent_id="agent-1",
            details={"text": "pivot off the frontend"},
        )
        assert outcome.applied is True
        assert outcome.artifact_id is not None
        # Guard against the directive returning ``applied=True`` without
        # actually persisting an interrupt the agent can consume.
        pending = await store.get(outcome.artifact_id)
        assert pending is not None
        assert pending.type is InterruptType.INFO_REQUEST

    async def test_pause_not_handled(self) -> None:
        store = InterruptStore()
        directive = SafeDefaultSteeringDirective(store, clock=FakeClock())

        outcome = await directive.steer(
            kind=InterventionKind.PAUSE,
            execution_id="exec-1",
            agent_id="agent-1",
            details={},
        )
        assert outcome.applied is False
        assert outcome.artifact_id is None

    async def test_empty_text_not_applied(self) -> None:
        store = InterruptStore()
        directive = SafeDefaultSteeringDirective(store, clock=FakeClock())

        outcome = await directive.steer(
            kind=InterventionKind.HINT,
            execution_id="exec-1",
            agent_id="agent-1",
            details={"text": "   "},
        )
        assert outcome.applied is False

    def test_factory_unknown_strategy_raises(self) -> None:
        store = InterruptStore()
        with pytest.raises(ValueError, match="Unknown steering directive"):
            build_steering_directive(store, strategy="bogus")

    def test_factory_builds_default(self) -> None:
        store = InterruptStore()
        directive = build_steering_directive(store, clock=FakeClock())
        assert isinstance(directive, SafeDefaultSteeringDirective)
