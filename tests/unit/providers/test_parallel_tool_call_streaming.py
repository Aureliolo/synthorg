"""Two tools asked for in one turn must arrive as two tools.

``index`` is what a streaming client associates argument fragments by. Drop
it and a client has only the arrival order, so the fragments of every parallel
call in the turn land in one slot and concatenate: ``{"path":"README.md"}``
followed by ``{"command":"pytest"}`` parses as neither. The calls are dropped,
the turn asks for tools and delivers none, and the run dies without a single
message saying a tool call went missing.

That is not hypothetical: our own gateway omitted the field, and a 54-run A/B
recording lost ten native-loop rows to it while the model itself was emitting
perfectly well-formed calls the whole time.
"""

import json
from types import SimpleNamespace

import pytest

from synthorg.api.gateway.service import _tool_call_index
from synthorg.api.gateway.translation import stream_chunk_to_openai
from synthorg.providers.drivers.litellm_tool_accumulator import (
    _ToolCallAccumulator,
    accumulate_tool_call_deltas,
)
from synthorg.providers.enums import StreamEventType
from synthorg.providers.models import StreamChunk, ToolCall

pytestmark = pytest.mark.unit

_READ = ToolCall(id="call_a", name="read_file", arguments={"path": "README.md"})
_SHELL = ToolCall(
    id="call_b", name="shell_command", arguments={"command": "python -m pytest -q"}
)


def _wire(calls: list[ToolCall]) -> list[dict[str, object]]:
    """Serialise *calls* the way one gateway response stream would.

    Args:
        calls: The tool calls the provider emitted, in order.

    Returns:
        One OpenAI tool-call object per call.
    """
    indices: dict[str, int] = {}
    payloads: list[dict[str, object]] = []
    for call in calls:
        chunk = StreamChunk(
            event_type=StreamEventType.TOOL_CALL_DELTA, tool_call_delta=call
        )
        body = stream_chunk_to_openai(
            chunk,
            response_id="chatcmpl-x",
            created=0,
            model="example-large-001",
            tool_call_index=_tool_call_index(chunk, indices),
        )
        assert body is not None
        choices = body["choices"]
        assert isinstance(choices, list)
        delta = choices[0]["delta"]
        payloads.append(delta["tool_calls"][0])
    return payloads


def _as_delta(payload: dict[str, object]) -> object:
    """Rebuild the attribute-access shape a streaming client reads.

    Args:
        payload: One serialised OpenAI tool-call object.

    Returns:
        An object exposing ``index`` only when the wire carried one.
    """
    function = payload["function"]
    assert isinstance(function, dict)
    fields: dict[str, object] = {
        "id": payload["id"],
        "function": SimpleNamespace(
            name=function["name"], arguments=function["arguments"]
        ),
    }
    if "index" in payload:
        fields["index"] = payload["index"]
    return SimpleNamespace(**fields)


class TestGatewayStreamsAnIndex:
    def test_a_streamed_call_carries_its_position(self) -> None:
        assert _wire([_READ])[0]["index"] == 0

    def test_parallel_calls_carry_distinct_positions(self) -> None:
        payloads = _wire([_READ, _SHELL])
        assert [p["index"] for p in payloads] == [0, 1]

    def test_a_position_is_stable_per_call_id(self) -> None:
        # Every fragment of one call must report the same position, or the
        # client splits one call across two slots and both halves fail to
        # parse.
        payloads = _wire([_READ, _SHELL, _READ])
        assert [p["index"] for p in payloads] == [0, 1, 0]


class TestClientReassembly:
    def test_parallel_calls_survive_the_round_trip(self) -> None:
        pending: dict[int, _ToolCallAccumulator] = {}
        accumulate_tool_call_deltas(
            [_as_delta(p) for p in _wire([_READ, _SHELL])], pending
        )

        built = [pending[idx].build() for idx in sorted(pending)]
        assert [call.name for call in built if call] == ["read_file", "shell_command"]
        assert [call.arguments for call in built if call] == [
            {"path": "README.md"},
            {"command": "python -m pytest -q"},
        ]


class TestUnindexedUpstream:
    """The client must not merge distinct calls when an upstream omits index."""

    def test_two_ids_without_an_index_get_their_own_slots(self) -> None:
        deltas = [
            SimpleNamespace(
                id="call_a",
                function=SimpleNamespace(
                    name="read_file", arguments=json.dumps({"path": "README.md"})
                ),
            ),
            SimpleNamespace(
                id="call_b",
                function=SimpleNamespace(
                    name="shell_command", arguments=json.dumps({"command": "pytest"})
                ),
            ),
        ]
        pending: dict[int, _ToolCallAccumulator] = {}

        accumulate_tool_call_deltas(list(deltas), pending)

        assert sorted(pending) == [0, 1]
        assert [pending[i].name for i in sorted(pending)] == [
            "read_file",
            "shell_command",
        ]

    def test_a_fragment_with_no_id_continues_the_current_call(self) -> None:
        # A continuation carries neither id nor index, so it can only mean the
        # call already in flight; opening a new slot would split one call's
        # arguments in half and lose both.
        deltas = [
            SimpleNamespace(
                id="call_a",
                function=SimpleNamespace(name="read_file", arguments='{"path":'),
            ),
            SimpleNamespace(function=SimpleNamespace(name=None, arguments='"a.md"}')),
        ]
        pending: dict[int, _ToolCallAccumulator] = {}

        accumulate_tool_call_deltas(list(deltas), pending)

        assert sorted(pending) == [0]
        built = pending[0].build()
        assert built is not None
        assert built.arguments == {"path": "a.md"}

    def test_repeating_an_id_reuses_its_slot(self) -> None:
        deltas = [
            SimpleNamespace(
                id="call_a",
                function=SimpleNamespace(name="read_file", arguments='{"path":'),
            ),
            SimpleNamespace(
                id="call_b",
                function=SimpleNamespace(name="shell_command", arguments='{"cmd":'),
            ),
            SimpleNamespace(
                id="call_a", function=SimpleNamespace(name=None, arguments='"a.md"}')
            ),
        ]
        pending: dict[int, _ToolCallAccumulator] = {}

        accumulate_tool_call_deltas(list(deltas), pending)

        first = pending[0].build()
        assert first is not None
        assert first.arguments == {"path": "a.md"}
