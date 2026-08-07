# module-kind: tests
"""Unit tests for the streamed unified turn (`stream_turn_events`)."""

import json
from collections.abc import AsyncIterator

import pytest

from synthorg.api.controllers import _turn_stream
from synthorg.api.controllers._turn_dispatch import ExplainContext, TurnRequest
from synthorg.api.state import AppState
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff._multi_voice import ChimeIn
from synthorg.meta.chief_of_staff.intent_router import (
    IntentOutcome,
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.models import (
    ChatAnswerComplete,
    ChatAnswerDelta,
    ChatQuery,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit

# All app-state-facing work is patched below, so a spec'd stand-in that passes
# the ``AppState`` boundary type check is all the generator needs.
_APP = mock_of[AppState]()


class _FakeChatBackend:
    """A chat backend whose `ask_stream` replays scripted delta/complete events."""

    def __init__(self, deltas: tuple[str, ...], answer: str) -> None:
        self._deltas = deltas
        self._answer = answer

    async def ask_stream(
        self, query: object, snapshot: object, *, org_state: object = None
    ) -> AsyncIterator[ChatAnswerDelta | ChatAnswerComplete]:
        del query, snapshot, org_state
        for delta in self._deltas:
            yield ChatAnswerDelta(delta=delta)
        yield ChatAnswerComplete(answer=NotBlankStr(self._answer), sources=())


async def _collect(gen: AsyncIterator[dict[str, str]]) -> list[dict[str, str]]:
    return [frame async for frame in gen]


#: A classified outcome names the model that produced it.
_CLASSIFIER_MODEL = "example-medium-001"


def _explain_outcome() -> IntentOutcome:
    return IntentOutcome(
        intent=TurnIntent.EXPLAIN,
        reason=IntentRoutingReason.CLASSIFIED,
        confidence=0.9,
        model=NotBlankStr(_CLASSIFIER_MODEL),
    )


def _patch_intent(monkeypatch: pytest.MonkeyPatch, outcome: IntentOutcome) -> None:
    async def _fake(app_state: object, **kwargs: object) -> IntentOutcome:
        del app_state, kwargs
        return outcome

    monkeypatch.setattr(_turn_stream, "resolve_turn_intent", _fake)


def _patch_explain_context(
    monkeypatch: pytest.MonkeyPatch, backend: _FakeChatBackend
) -> None:
    async def _fake(app_state: object, *, body: str) -> ExplainContext:
        del app_state, body
        return ExplainContext(
            chat_backend=backend,  # type: ignore[arg-type]  # structural stand-in
            query=ChatQuery(question=NotBlankStr("q")),
            snapshot=None,  # type: ignore[arg-type]  # unused by the fake backend
            org_state=None,
        )

    monkeypatch.setattr(_turn_stream, "prepare_explain_context", _fake)


def _patch_chimes(monkeypatch: pytest.MonkeyPatch, chimes: tuple[ChimeIn, ...]) -> None:
    async def _fake(app_state: object, **kwargs: object) -> tuple[ChimeIn, ...]:
        del app_state, kwargs
        return chimes

    monkeypatch.setattr(_turn_stream, "resolve_chime_ins", _fake)


_REQUEST = TurnRequest(message=NotBlankStr("How is our runway?"))


class TestStreamTurnEvents:
    async def test_explain_streams_deltas_then_complete_then_chime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_intent(monkeypatch, _explain_outcome())
        _patch_explain_context(
            monkeypatch, _FakeChatBackend(("Runway ", "is fine."), "Runway is fine.")
        )
        _patch_chimes(
            monkeypatch,
            (ChimeIn(role="CFO", name="Casey", content="Watch Q3."),),
        )
        frames = await _collect(_turn_stream.stream_turn_events(_APP, data=_REQUEST))
        events = [f["event"] for f in frames]
        assert events == ["delta", "delta", "complete", "chime"]
        assert json.loads(frames[0]["data"]) == {"delta": "Runway "}
        complete = json.loads(frames[2]["data"])
        assert complete["intent"] == "explain"
        assert complete["answer"]["answer"] == "Runway is fine."
        assert json.loads(frames[3]["data"])["name"] == "Casey"

    async def test_non_explain_emits_single_deferred_frame(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_intent(
            monkeypatch,
            IntentOutcome(
                intent=TurnIntent.PROPOSE,
                reason=IntentRoutingReason.CLASSIFIED,
                confidence=0.9,
                model=NotBlankStr(_CLASSIFIER_MODEL),
            ),
        )
        frames = await _collect(_turn_stream.stream_turn_events(_APP, data=_REQUEST))
        assert [f["event"] for f in frames] == ["deferred"]
        assert json.loads(frames[0]["data"])["intent"] == "propose"

    async def test_failure_after_start_yields_error_frame(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(app_state: object, **kwargs: object) -> IntentOutcome:
            del app_state, kwargs
            msg = "classifier exploded"
            raise RuntimeError(msg)

        monkeypatch.setattr(_turn_stream, "resolve_turn_intent", _boom)
        frames = await _collect(_turn_stream.stream_turn_events(_APP, data=_REQUEST))
        assert [f["event"] for f in frames] == ["error"]
        data = json.loads(frames[0]["data"])
        assert "error" in data
        # A non-domain error carries only the generic message (no structured
        # detail to leak).
        assert "error_detail" not in data

    async def test_domain_error_after_start_yields_structured_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _unavailable(app_state: object, **kwargs: object) -> IntentOutcome:
            del app_state, kwargs
            raise ServiceUnavailableError

        monkeypatch.setattr(_turn_stream, "resolve_turn_intent", _unavailable)
        frames = await _collect(_turn_stream.stream_turn_events(_APP, data=_REQUEST))
        assert [f["event"] for f in frames] == ["error"]
        data = json.loads(frames[0]["data"])
        # A domain error restores the structured detail the buffered turn emits,
        # so the client surfaces the same fail-closed / retry UX.
        detail = data["error_detail"]
        assert detail["error_code"] == ErrorCode.SERVICE_UNAVAILABLE.value
        assert detail["retryable"] is True
