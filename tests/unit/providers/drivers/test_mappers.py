"""Unit tests for provider driver mapping functions."""

import pytest

from synthorg.providers.drivers.mappers import (
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
        raw = [
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
        raw = [
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

    def test_invalid_json_arguments_returns_empty_dict(self) -> None:
        raw = [
            {
                "id": "call_001",
                "function": {"name": "test", "arguments": "not-valid-json"},
            },
        ]
        result = extract_tool_calls(raw)

        assert result[0].arguments == {}

    def test_pre_parsed_dict_arguments(self) -> None:
        raw = [
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

    def test_missing_function_skips_entry(self) -> None:
        raw = [{"id": "call_001"}]
        result = extract_tool_calls(raw)

        assert result == ()

    def test_missing_id_skips_entry(self) -> None:
        raw = [{"function": {"name": "test", "arguments": "{}"}}]
        result = extract_tool_calls(raw)

        assert result == ()
