"""Unit tests for the OpenHands container runtime's pure helpers.

The container spawn itself is docker-gated (live smoke); these cover the
event-normalization + spec-serialisation logic with no SDK or Docker.
"""

import asyncio
import json
from collections.abc import AsyncGenerator

import pytest

from synthorg.engine.openhands.container_runtime import (
    _non_negative_float,
    _non_negative_int,
    _parse_event,
    _RunningTotals,
    _spec_line,
    build_container_conversation,
)
from synthorg.engine.openhands.conversation import (
    EventSink,
    OpenHandsOutcome,
    OpenHandsRunSpec,
)
from synthorg.engine.openhands.errors import OpenHandsRuntimeError
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind
from synthorg.tools.sandbox.errors import SandboxError
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

_CONVERSATION_ID = as_uuid("c-1")


def _spec() -> OpenHandsRunSpec:
    return OpenHandsRunSpec(
        task_prompt="do the thing",
        model="example-large-001",
        gateway_base_url="http://gateway/v1",
        gateway_token="tok",
        mcp_base_url="http://mcp",
        workspace_path="/workspace",
        conversation_id=_CONVERSATION_ID,
        max_turns=7,
        project_id="proj-1",
        system_prompt="HOUSE STYLE: no em-dashes.",
    )


def test_every_container_facing_field_reaches_the_container() -> None:
    """The payload is an allowlist, so a new field is dropped by default.

    That is the safe default for a boundary and the wrong one for a field the
    agent's behaviour depends on: the system prompt was added to the spec, was
    not added here, and the harness ran on the SDK's stock prompt while the
    scoreboard read the difference as a property of the loop.
    """
    payload = json.loads(_spec_line(_spec()))
    host_only = {"project_id"}

    missing = set(OpenHandsRunSpec.model_fields) - host_only - set(payload)

    assert not missing, f"spec fields never reach the container: {sorted(missing)}"


def test_spec_line_excludes_host_only_fields() -> None:
    line = _spec_line(_spec())
    assert line.endswith("\n")
    payload = json.loads(line)
    # project_id stays host-side (mount selection), never sent to the container.
    assert "project_id" not in payload
    assert payload["conversation_id"] == str(_CONVERSATION_ID)
    assert payload["max_turns"] == 7
    assert payload["gateway_base_url"] == "http://gateway/v1"
    assert payload["gateway_token"] == "tok"
    assert payload["mcp_base_url"] == "http://mcp"


def test_parse_event_action_carries_tool_and_cost_delta() -> None:
    event, totals = _parse_event(
        json.dumps({"kind": "action", "tool_name": "terminal", "cost": 0.30}),
        _RunningTotals(cost=0.10),
    )
    assert event is not None
    assert event.kind is OpenHandsEventKind.ACTION
    assert event.tool_name == "terminal"
    assert event.cost == pytest.approx(0.20)  # delta from prev 0.10
    assert totals.cost == pytest.approx(0.30)


def test_parse_event_message_gets_cost_but_no_tool() -> None:
    event, _ = _parse_event(
        json.dumps({"kind": "message", "text": "hi", "tool_name": "x", "cost": 0.5}),
        _RunningTotals(),
    )
    assert event is not None
    assert event.kind is OpenHandsEventKind.MESSAGE
    assert event.tool_name is None  # tool_name only survives on ACTION
    assert event.cost == pytest.approx(0.5)


def test_parse_event_tool_error_names_its_tool_and_bills_nothing() -> None:
    """A rejected call reaches the loop as itself, not as an unknown kind.

    Unmapped, it falls through the parser's skew branch and is dropped, and the
    loop never learns the call was refused. The line carries the run's running
    totals like every other, so the figures must stay with the turn that
    actually spent them.
    """
    event, totals = _parse_event(
        json.dumps(
            {
                "kind": "tool_error",
                "text": "Tool 'shel' not found",
                "tool_name": "shel",
                "cost": 0.4,
                "input_tokens": 90,
                "output_tokens": 12,
            }
        ),
        # Deliberately behind the line's figures: previous totals equal to them
        # make every delta zero whatever the kind, so a TOOL_ERROR billed like
        # an ACTION would still read as zero and pass.
        _RunningTotals(cost=0.1, input_tokens=40, output_tokens=2),
    )

    assert event is not None
    assert event.kind is OpenHandsEventKind.TOOL_ERROR
    assert event.tool_name == "shel"
    assert event.cost == 0.0
    assert event.input_tokens == 0
    assert event.output_tokens == 0
    # The line's totals still advance the running figures: they are the run's,
    # not this event's, and the next turn is measured against them.
    assert totals.cost == pytest.approx(0.4)


def test_parse_event_observation_has_no_cost() -> None:
    event, totals = _parse_event(
        json.dumps({"kind": "observation", "cost": 0.9}), _RunningTotals(cost=0.2)
    )
    assert event is not None
    assert event.kind is OpenHandsEventKind.OBSERVATION
    assert event.cost == 0.0  # non-turn kinds carry no cost
    assert totals.cost == pytest.approx(0.9)  # accumulator still advances


def test_parse_event_finished_carries_no_cost() -> None:
    event, _ = _parse_event(
        json.dumps({"kind": "finished", "cost": 1.5}), _RunningTotals()
    )
    assert event is not None
    assert event.kind is OpenHandsEventKind.FINISHED
    assert event.cost == 0.0


def test_parse_event_action_carries_token_deltas() -> None:
    # The rubric ranks on tokens and scores a zero as unbeatable, so a turn
    # reporting none would win that dimension by reporting nothing at all.
    event, totals = _parse_event(
        json.dumps(
            {
                "kind": "action",
                "tool_name": "terminal",
                "cost": 0.3,
                "input_tokens": 900,
                "output_tokens": 150,
            }
        ),
        _RunningTotals(cost=0.1, input_tokens=400, output_tokens=50),
    )
    assert event is not None
    assert event.input_tokens == 500
    assert event.output_tokens == 100
    assert totals.input_tokens == 900
    assert totals.output_tokens == 150


def test_parse_event_token_deltas_sum_to_the_reported_total() -> None:
    # The container reports running totals and the loop accumulates per-turn
    # deltas, so the two only agree if every delta is measured against the
    # previous event rather than restated as the total.
    lines = [
        json.dumps({"kind": "message", "input_tokens": 100, "output_tokens": 10}),
        json.dumps({"kind": "action", "input_tokens": 260, "output_tokens": 45}),
        json.dumps({"kind": "message", "input_tokens": 400, "output_tokens": 80}),
    ]
    totals = _RunningTotals()
    events: list[OpenHandsEvent] = []
    for line in lines:
        event, totals = _parse_event(line, totals)
        assert event is not None
        events.append(event)

    assert sum(e.input_tokens for e in events) == totals.input_tokens == 400
    assert sum(e.output_tokens for e in events) == totals.output_tokens == 80


def test_parse_event_tokens_only_land_on_turn_kinds() -> None:
    # A tool result is not an LLM turn, and the event model rejects token
    # figures on one, so the delta has to be withheld rather than clamped.
    event, totals = _parse_event(
        json.dumps({"kind": "observation", "input_tokens": 90, "output_tokens": 9}),
        _RunningTotals(input_tokens=10, output_tokens=1),
    )
    assert event is not None
    assert event.input_tokens == 0
    assert event.output_tokens == 0
    assert totals.input_tokens == 90  # the accumulator still advances


def test_parse_event_token_total_going_backwards_yields_no_negative_delta() -> None:
    event, totals = _parse_event(
        json.dumps({"kind": "message", "input_tokens": 5, "output_tokens": 1}),
        _RunningTotals(input_tokens=50, output_tokens=10),
    )
    assert event is not None
    assert event.input_tokens == 0
    assert event.output_tokens == 0
    assert totals.input_tokens == 5


def test_parse_event_missing_or_malformed_token_fields_default_to_zero() -> None:
    event, totals = _parse_event(
        json.dumps({"kind": "message", "input_tokens": "many"}), _RunningTotals()
    )
    assert event is not None
    assert event.input_tokens == 0
    assert event.output_tokens == 0
    assert totals.input_tokens == 0


def test_parse_event_unparseable_line_is_skipped() -> None:
    event, totals = _parse_event("not json", _RunningTotals(cost=0.4))
    assert event is None
    assert totals.cost == pytest.approx(0.4)


def test_parse_event_unknown_kind_is_skipped() -> None:
    event, totals = _parse_event(
        json.dumps({"kind": "heartbeat"}), _RunningTotals(cost=0.4)
    )
    assert event is None
    assert totals.cost == pytest.approx(0.4)


def test_parse_event_unmapped_kind_is_skipped_and_named() -> None:
    # The container reports an SDK event class it neither forwards nor knows
    # to ignore. It carries no turn, so nothing reaches the loop, but the
    # class name has to survive into the log for the skew to be diagnosable.
    event, totals = _parse_event(
        json.dumps({"kind": "unmapped", "text": "SomeNewSdkEvent"}),
        _RunningTotals(cost=0.4),
    )
    assert event is None
    assert totals.cost == pytest.approx(0.4)


def test_parse_event_non_dict_payload_is_skipped() -> None:
    # Valid JSON but not an object (e.g. a bare array) is skipped, not crashed.
    event, totals = _parse_event(json.dumps([1, 2, 3]), _RunningTotals(cost=0.7))
    assert event is None
    assert totals.cost == pytest.approx(0.7)


def test_non_negative_float_clamps_and_defaults() -> None:
    assert _non_negative_float(-3.0) == 0.0
    assert _non_negative_float("nan-ish") == 0.0
    assert _non_negative_float(None) == 0.0
    assert _non_negative_float(2) == pytest.approx(2.0)
    # ``bool`` is an ``int`` subclass, and True would silently read as $1.00 of
    # spend, which the budget kill and the loop ranking both act on.
    assert _non_negative_float(True) == 0.0


def test_non_negative_int_clamps_and_defaults() -> None:
    assert _non_negative_int(-3) == 0
    assert _non_negative_int("many") == 0
    assert _non_negative_int(None) == 0
    assert _non_negative_int(7) == 7
    # A float total (a JSON number the container rounded) truncates rather
    # than raising at the event model's integer boundary.
    assert _non_negative_int(7.9) == 7
    # ``bool`` is an ``int`` subclass, and True would silently read as 1 token.
    assert _non_negative_int(True) == 0


async def test_build_container_conversation_is_lazy() -> None:
    # Building the conversation must not spawn a container; the streaming
    # generator is only created when run() is awaited.
    calls: list[str] = []

    class _FakeSandbox:
        def stream_container_task(self, **_kwargs: object) -> object:
            calls.append("stream")
            pytest.fail("must not stream at build time")

    async def _sink(_event: object) -> bool:
        return True

    conversation = await build_container_conversation(
        _FakeSandbox(),  # type: ignore[arg-type]  # structural SandboxStreamer
        600.0,
        3600.0,
        _spec(),
        _sink,
    )
    assert conversation is not None
    assert calls == []


class _ScriptedSandbox:
    """A ``SandboxStreamer`` fake yielding scripted stdout lines.

    Tracks teardown (``closed``) so a test can assert ``run()`` drives the
    generator's ``aclose()`` on every exit path, and optionally raises a
    scripted exception mid-stream to exercise the error mapping.
    """

    def __init__(
        self, lines: list[str], *, raise_after: Exception | None = None
    ) -> None:
        self._lines = lines
        self._raise_after = raise_after
        self.closed = False

    def stream_container_task(self, **_kwargs: object) -> AsyncGenerator[str]:
        return self._gen()

    async def _gen(self) -> AsyncGenerator[str]:
        try:
            for line in self._lines:
                yield line
            if self._raise_after is not None:
                raise self._raise_after
        finally:
            self.closed = True


async def _drive(sandbox: _ScriptedSandbox, sink: EventSink) -> OpenHandsOutcome:
    conversation = await build_container_conversation(
        sandbox,  # structural SandboxStreamer
        600.0,
        3600.0,
        _spec(),
        sink,
    )
    return await conversation.run()


async def test_run_enforces_wall_clock_cap() -> None:
    # A run that never yields a terminal event is force-ended by the total
    # wall-clock cap (configured below the bearer TTL), surfacing as a runtime
    # error and tearing the stream down, rather than hanging until the token
    # expires.
    hang = asyncio.Event()

    class _HangingSandbox:
        def __init__(self) -> None:
            self.closed = False

        def stream_container_task(self, **_kwargs: object) -> AsyncGenerator[str]:
            return self._gen()

        async def _gen(self) -> AsyncGenerator[str]:
            try:
                await hang.wait()
                yield ""  # unreachable: the cap trips while awaiting the event
            finally:
                self.closed = True

    async def _sink(_event: object) -> bool:
        return True

    sandbox = _HangingSandbox()
    conversation = await build_container_conversation(
        sandbox,  # structural SandboxStreamer
        600.0,
        0.05,
        _spec(),
        _sink,
    )
    with pytest.raises(OpenHandsRuntimeError):
        await conversation.run()
    assert sandbox.closed is True


async def test_run_forwards_events_and_finishes() -> None:
    received: list[OpenHandsEventKind] = []

    async def _sink(event: OpenHandsEvent) -> bool:
        received.append(event.kind)
        return True

    sandbox = _ScriptedSandbox(
        [
            json.dumps({"kind": "action", "tool_name": "terminal", "cost": 0.1}) + "\n",
            json.dumps({"kind": "message", "text": "done", "cost": 0.2}) + "\n",
            json.dumps({"kind": "finished", "cost": 0.2}) + "\n",
        ]
    )
    outcome = await _drive(sandbox, _sink)
    assert outcome.finished is True
    assert received == [
        OpenHandsEventKind.ACTION,
        OpenHandsEventKind.MESSAGE,
        OpenHandsEventKind.FINISHED,
    ]
    assert sandbox.closed is True  # aclose() drove teardown


async def test_run_stops_and_tears_down_when_sink_returns_false() -> None:
    received: list[OpenHandsEventKind] = []

    async def _sink(event: OpenHandsEvent) -> bool:
        received.append(event.kind)
        return False  # stop after the first event

    sandbox = _ScriptedSandbox(
        [
            json.dumps({"kind": "action", "cost": 0.1}) + "\n",
            json.dumps({"kind": "message", "text": "unreached"}) + "\n",
        ]
    )
    outcome = await _drive(sandbox, _sink)
    assert outcome.finished is False
    assert received == [OpenHandsEventKind.ACTION]  # stopped before the second
    assert sandbox.closed is True


async def test_run_wraps_sink_exception_as_runtime_error() -> None:
    async def _sink(_event: OpenHandsEvent) -> bool:
        msg = "boundary checker bug"
        raise ValueError(msg)

    sandbox = _ScriptedSandbox([json.dumps({"kind": "action", "cost": 0.1}) + "\n"])
    with pytest.raises(OpenHandsRuntimeError):
        await _drive(sandbox, _sink)
    assert sandbox.closed is True


async def test_run_maps_sandbox_error_to_runtime_error() -> None:
    async def _sink(_event: OpenHandsEvent) -> bool:
        return True

    sandbox = _ScriptedSandbox(
        [json.dumps({"kind": "message", "text": "hi"}) + "\n"],
        raise_after=SandboxError("container died"),
    )
    with pytest.raises(OpenHandsRuntimeError):
        await _drive(sandbox, _sink)
    assert sandbox.closed is True
