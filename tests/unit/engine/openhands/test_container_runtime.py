"""Unit tests for the OpenHands container runtime's pure helpers.

The container spawn itself is docker-gated (live smoke); these cover the
event-normalization + spec-serialisation logic with no SDK or Docker.
"""

import json

import pytest

from synthorg.engine.openhands.container_runtime import (
    _non_negative_float,
    _parse_event,
    _spec_line,
    build_container_conversation,
)
from synthorg.engine.openhands.conversation import OpenHandsRunSpec
from synthorg.engine.openhands.events import OpenHandsEventKind

pytestmark = pytest.mark.unit


def _spec() -> OpenHandsRunSpec:
    return OpenHandsRunSpec(
        task_prompt="do the thing",
        model="example-large-001",
        gateway_base_url="http://gateway/v1",
        gateway_token="tok",
        mcp_base_url="http://mcp",
        workspace_path="/workspace",
        conversation_id="c-1",
        max_turns=7,
        project_id="proj-1",
    )


def test_spec_line_excludes_host_only_fields() -> None:
    line = _spec_line(_spec())
    assert line.endswith("\n")
    payload = json.loads(line)
    # project_id stays host-side (mount selection), never sent to the container.
    assert "project_id" not in payload
    assert payload["conversation_id"] == "c-1"
    assert payload["max_turns"] == 7
    assert payload["gateway_token"] == "tok"


def test_parse_event_action_carries_tool_and_cost_delta() -> None:
    event, accumulated = _parse_event(
        json.dumps({"kind": "action", "tool_name": "terminal", "cost": 0.30}), 0.10
    )
    assert event is not None
    assert event.kind is OpenHandsEventKind.ACTION
    assert event.tool_name == "terminal"
    assert event.cost == pytest.approx(0.20)  # delta from prev 0.10
    assert accumulated == pytest.approx(0.30)


def test_parse_event_message_gets_cost_but_no_tool() -> None:
    event, _ = _parse_event(
        json.dumps({"kind": "message", "text": "hi", "tool_name": "x", "cost": 0.5}),
        0.0,
    )
    assert event is not None
    assert event.kind is OpenHandsEventKind.MESSAGE
    assert event.tool_name is None  # tool_name only survives on ACTION
    assert event.cost == pytest.approx(0.5)


def test_parse_event_observation_has_no_cost() -> None:
    event, accumulated = _parse_event(
        json.dumps({"kind": "observation", "cost": 0.9}), 0.2
    )
    assert event is not None
    assert event.kind is OpenHandsEventKind.OBSERVATION
    assert event.cost == 0.0  # non-turn kinds carry no cost
    assert accumulated == pytest.approx(0.9)  # accumulator still advances


def test_parse_event_finished_carries_no_cost() -> None:
    event, _ = _parse_event(json.dumps({"kind": "finished", "cost": 1.5}), 0.0)
    assert event is not None
    assert event.kind is OpenHandsEventKind.FINISHED
    assert event.cost == 0.0


def test_parse_event_unparseable_line_is_skipped() -> None:
    event, prev = _parse_event("not json", 0.4)
    assert event is None
    assert prev == pytest.approx(0.4)


def test_parse_event_unknown_kind_is_skipped() -> None:
    event, prev = _parse_event(json.dumps({"kind": "heartbeat"}), 0.4)
    assert event is None
    assert prev == pytest.approx(0.4)


def test_non_negative_float_clamps_and_defaults() -> None:
    assert _non_negative_float(-3.0) == 0.0
    assert _non_negative_float("nan-ish") == 0.0
    assert _non_negative_float(None) == 0.0
    assert _non_negative_float(2) == pytest.approx(2.0)


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
        _spec(),
        _sink,
    )
    assert conversation is not None
    assert calls == []
