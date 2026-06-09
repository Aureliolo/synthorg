"""Unit tests for provider driver mapping functions."""

import pytest

from synthorg.providers.drivers.mappers import (
    extract_retry_after,
    extract_tool_calls,
    map_finish_reason,
    messages_to_dicts,
    tools_to_dicts,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

# ── messages_to_dicts ────────────────────────────────────────────


@pytest.mark.unit
class TestMessagesToDicts:
    def test_system_message(self) -> None:
        msg = ChatMessage(role=MessageRole.SYSTEM, content="You are helpful.")
        result = messages_to_dicts([msg])

        assert result == [{"role": "system", "content": "You are helpful."}]

    def test_user_message(self) -> None:
        msg = ChatMessage(role=MessageRole.USER, content="Hello!")
        result = messages_to_dicts([msg])

        assert result == [{"role": "user", "content": "Hello!"}]

    def test_assistant_message_text_only(self) -> None:
        msg = ChatMessage(role=MessageRole.ASSISTANT, content="Hi there!")
        result = messages_to_dicts([msg])

        assert result == [{"role": "assistant", "content": "Hi there!"}]

    def test_assistant_message_with_tool_calls(self) -> None:
        tc = ToolCall(
            id="call_001",
            name="get_weather",
            arguments={"location": "London"},
        )
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=(tc,),
        )
        result = messages_to_dicts([msg])

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert "content" not in result[0]
        raw_tool_calls = result[0]["tool_calls"]
        assert isinstance(raw_tool_calls, list)
        assert len(raw_tool_calls) == 1
        tc = raw_tool_calls[0]
        assert isinstance(tc, dict)
        assert tc["id"] == "call_001"
        assert tc["type"] == "function"
        func = tc["function"]
        assert isinstance(func, dict)
        assert func["name"] == "get_weather"
        assert func["arguments"] == '{"location": "London"}'

    def test_tool_result_message(self) -> None:
        msg = ChatMessage(
            role=MessageRole.TOOL,
            tool_result=ToolResult(
                tool_call_id="call_001",
                content="Sunny, 22°C",
            ),
        )
        result = messages_to_dicts([msg])

        assert result == [
            {
                "role": "tool",
                "content": "Sunny, 22°C",
                "tool_call_id": "call_001",
            },
        ]

    def test_multiple_messages_preserve_order(self) -> None:
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content="System prompt"),
            ChatMessage(role=MessageRole.USER, content="Question"),
            ChatMessage(role=MessageRole.ASSISTANT, content="Answer"),
        ]
        result = messages_to_dicts(messages)

        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"

    def test_empty_messages_list(self) -> None:
        assert messages_to_dicts([]) == []


# ── tools_to_dicts ───────────────────────────────────────────────


@pytest.mark.unit
class TestToolsToDicts:
    def test_single_tool(self) -> None:
        tool = ToolDefinition(
            name="search",
            description="Search the codebase",
            parameters_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        result = tools_to_dicts([tool])

        assert len(result) == 1
        assert result[0] == {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the codebase",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }

    def test_tool_with_empty_schema(self) -> None:
        tool = ToolDefinition(name="ping", description="Ping the server")
        result = tools_to_dicts([tool])

        func = result[0]["function"]
        assert isinstance(func, dict)
        assert func["parameters"] == {}

    def test_multiple_tools(self) -> None:
        tools = [
            ToolDefinition(name="a", description="Tool A"),
            ToolDefinition(name="b", description="Tool B"),
        ]
        result = tools_to_dicts(tools)

        assert len(result) == 2
        func_0 = result[0]["function"]
        func_1 = result[1]["function"]
        assert isinstance(func_0, dict)
        assert isinstance(func_1, dict)
        assert func_0["name"] == "a"
        assert func_1["name"] == "b"

    def test_empty_tools_list(self) -> None:
        assert tools_to_dicts([]) == []


# ── map_finish_reason ────────────────────────────────────────────


@pytest.mark.unit
class TestMapFinishReason:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("stop", FinishReason.STOP),
            ("end_turn", FinishReason.STOP),
            ("stop_sequence", FinishReason.STOP),
            ("length", FinishReason.MAX_TOKENS),
            ("max_tokens", FinishReason.MAX_TOKENS),
            ("tool_calls", FinishReason.TOOL_USE),
            ("function_call", FinishReason.TOOL_USE),
            ("tool_use", FinishReason.TOOL_USE),
            ("content_filter", FinishReason.CONTENT_FILTER),
        ],
    )
    def test_known_reasons(self, raw: str, expected: FinishReason) -> None:
        assert map_finish_reason(raw) == expected

    def test_none_maps_to_error(self) -> None:
        assert map_finish_reason(None) == FinishReason.ERROR

    def test_unknown_string_maps_to_error(self) -> None:
        assert map_finish_reason("some_unknown_reason") == FinishReason.ERROR


# ── extract_tool_calls ───────────────────────────────────────────


@pytest.mark.unit
class TestExtractToolCalls:
    def test_none_returns_empty_tuple(self) -> None:
        assert extract_tool_calls(None) == ()

    def test_empty_list_returns_empty_tuple(self) -> None:
        assert extract_tool_calls([]) == ()

    def test_single_tool_call_from_dict(self) -> None:
        raw: list[object] = [
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "London"}',
                },
            },
        ]
        result = extract_tool_calls(raw)

        assert len(result) == 1
        assert result[0].id == "call_001"
        assert result[0].name == "get_weather"
        assert result[0].arguments == {"location": "London"}

    def test_tool_call_from_object(self) -> None:
        """Handle LiteLLM response objects with attribute access."""
        from unittest.mock import MagicMock

        func = MagicMock()
        func.name = "search"
        func.arguments = '{"query": "test"}'

        tc = MagicMock()
        tc.id = "call_002"
        tc.function = func

        result = extract_tool_calls([tc])

        assert len(result) == 1
        assert result[0].id == "call_002"
        assert result[0].name == "search"
        assert result[0].arguments == {"query": "test"}

    def test_multiple_tool_calls(self) -> None:
        raw: list[object] = [
            {
                "id": "call_001",
                "function": {"name": "a", "arguments": "{}"},
            },
            {
                "id": "call_002",
                "function": {"name": "b", "arguments": '{"x": 1}'},
            },
        ]
        result = extract_tool_calls(raw)

        assert len(result) == 2
        assert result[0].name == "a"
        assert result[1].name == "b"

    def test_invalid_json_arguments_drops_tool_call(self) -> None:
        """Unparseable JSON arguments drop the tool call (not emit empty args).

        Matches the streaming accumulator: a tool never runs with
        silently-emptied arguments in either path.
        """
        raw: list[object] = [
            {
                "id": "call_001",
                "function": {"name": "test", "arguments": "not-valid-json"},
            },
        ]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_pre_parsed_dict_arguments(self) -> None:
        raw: list[object] = [
            {
                "id": "call_001",
                "function": {
                    "name": "test",
                    "arguments": {"key": "value"},
                },
            },
        ]
        result = extract_tool_calls(raw)

        assert result[0].arguments == {"key": "value"}

    def test_non_finite_string_arguments_drops_tool_call(self) -> None:
        """JSON args with an ``Infinity`` literal drop the tool call.

        ``json.loads`` accepts ``Infinity`` by default, but ``ToolCall``
        forbids non-finite floats (``allow_inf_nan=False``); the gate in
        ``_parse_arguments`` drops the call so a tool never runs with
        silently-emptied arguments, instead of raising at construction.
        """
        raw: list[object] = [
            {
                "id": "call_001",
                "function": {"name": "test", "arguments": '{"score": Infinity}'},
            },
        ]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_non_finite_nested_dict_arguments_drops_tool_call(self) -> None:
        """Pre-parsed args carrying a nested non-finite float drop the call."""
        raw: list[object] = [
            {
                "id": "call_001",
                "function": {
                    "name": "test",
                    "arguments": {"grades": {"correctness": float("nan")}},
                },
            },
        ]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_non_dict_non_str_arguments_drops_tool_call(self) -> None:
        """Arguments that are neither a JSON string nor a dict drop the call."""
        raw: list[object] = [
            {
                "id": "call_001",
                "function": {"name": "test", "arguments": [1, 2, 3]},
            },
        ]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_missing_function_skips_entry(self) -> None:
        raw: list[object] = [{"id": "call_001"}]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_missing_id_skips_entry(self) -> None:
        raw: list[object] = [{"function": {"name": "test", "arguments": "{}"}}]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_non_str_id_skips_entry(self) -> None:
        """A non-string id (e.g. a malformed numeric id) is skipped."""
        raw: list[object] = [
            {"id": 123, "function": {"name": "test", "arguments": "{}"}},
        ]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_non_str_name_skips_entry(self) -> None:
        """A non-string function name is skipped rather than coerced."""
        raw: list[object] = [
            {"id": "call_001", "function": {"name": 7, "arguments": "{}"}},
        ]
        result = extract_tool_calls(raw)

        assert result == ()


# ── extract_retry_after ──────────────────────────────────────────


class _HeaderError(Exception):
    """Exception carrying HTTP headers for retry-after extraction tests."""

    def __init__(self, headers: object) -> None:
        super().__init__("rate limited")
        self.headers = headers


@pytest.mark.unit
class TestExtractRetryAfter:
    """Tests for ``extract_retry_after`` header parsing."""

    def test_valid_seconds_parsed(self) -> None:
        """A finite non-negative numeric header parses to a float."""
        assert extract_retry_after(_HeaderError({"Retry-After": "30"})) == 30.0

    def test_case_insensitive_header(self) -> None:
        """The lookup matches the header name case-insensitively."""
        assert extract_retry_after(_HeaderError({"retry-after": "5.5"})) == 5.5

    def test_no_headers_returns_none(self) -> None:
        """An exception without a mapping ``headers`` yields ``None``."""
        assert extract_retry_after(_HeaderError(None)) is None

    def test_missing_header_returns_none(self) -> None:
        """A headers mapping lacking retry-after yields ``None``."""
        assert extract_retry_after(_HeaderError({"X-Other": "1"})) is None

    def test_unparseable_value_returns_none(self) -> None:
        """A non-numeric header value yields ``None``."""
        assert extract_retry_after(_HeaderError({"Retry-After": "soon"})) is None

    @pytest.mark.parametrize("raw", ["inf", "-inf", "nan"])
    def test_non_finite_value_returns_none(self, raw: str) -> None:
        """``inf`` / ``nan`` parse as floats but are rejected as delays."""
        assert extract_retry_after(_HeaderError({"Retry-After": raw})) is None

    def test_negative_value_returns_none(self) -> None:
        """A negative retry-after delay is rejected."""
        assert extract_retry_after(_HeaderError({"Retry-After": "-5"})) is None
