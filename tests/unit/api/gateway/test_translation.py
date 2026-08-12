"""Unit tests for OpenAI <-> provider-model translation."""

import base64
import json

import pytest

from synthorg.api.gateway.translation import (
    parse_chat_request,
    response_to_openai,
    stream_chunk_to_openai,
    usage_chunk_to_openai,
)
from synthorg.core.completion_enums import FinishReason
from synthorg.core.domain_errors import ValidationError
from synthorg.providers.enums import MessageRole, StreamEventType
from synthorg.providers.models import (
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("stream_options", "expected"),
    [
        (None, False),
        ({}, False),
        ({"include_usage": False}, False),
        ({"include_usage": True}, True),
    ],
)
def test_parses_the_include_usage_request(
    stream_options: dict[str, object] | None, expected: bool
) -> None:
    raw: dict[str, object] = {
        "model": "example-provider/example-large-001",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    if stream_options is not None:
        raw["stream_options"] = stream_options

    assert parse_chat_request(raw).include_usage is expected


def test_usage_chunk_carries_counts_and_no_choices() -> None:
    chunk = usage_chunk_to_openai(
        TokenUsage(input_tokens=11, output_tokens=4, cost=0.5),
        response_id="chatcmpl-x",
        created=1,
        model="example-large-001",
    )

    assert chunk["choices"] == []
    assert chunk["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 4,
        "total_tokens": 15,
    }
    assert chunk["object"] == "chat.completion.chunk"


def test_parses_system_and_user_messages() -> None:
    parsed = parse_chat_request(
        {
            "model": "example-provider/example-large-001",
            "messages": [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "ship it"},
            ],
        }
    )

    assert parsed.model == "example-provider/example-large-001"
    assert [m.role for m in parsed.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]
    assert parsed.messages[1].content == "ship it"
    assert parsed.stream is False


def test_parses_assistant_tool_calls_with_json_string_arguments() -> None:
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "edit_file",
                                "arguments": '{"path": "a.py"}',
                            },
                        }
                    ],
                }
            ],
        }
    )

    call = parsed.messages[0].tool_calls[0]
    assert call.id == "call-1"
    assert call.name == "edit_file"
    assert call.arguments == {"path": "a.py"}


def test_parses_tool_result_message() -> None:
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [{"role": "tool", "tool_call_id": "call-1", "content": "done"}],
        }
    )

    result = parsed.messages[0].tool_result
    assert result is not None
    assert result.tool_call_id == "call-1"
    assert result.content == "done"


def test_maps_tools_and_sampling_config() -> None:
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "run",
                        "description": "run code",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "temperature": 0.2,
            "max_tokens": 128,
            "stop": ["END"],
        }
    )

    assert parsed.tools[0].name == "run"
    assert parsed.tools[0].parameters_schema == {"type": "object"}
    assert parsed.config is not None
    assert parsed.config.temperature == 0.2
    assert parsed.config.max_tokens == 128
    assert parsed.config.stop_sequences == ("END",)


def test_string_stop_becomes_single_sequence() -> None:
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "stop": "STOP",
        }
    )

    assert parsed.config is not None
    assert parsed.config.stop_sequences == ("STOP",)


def test_no_sampling_fields_yields_no_config() -> None:
    parsed = parse_chat_request(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    )

    assert parsed.config is None


def test_unknown_role_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        parse_chat_request(
            {"model": "m", "messages": [{"role": "wizard", "content": "hi"}]}
        )


def test_tool_message_without_id_raises() -> None:
    with pytest.raises(ValidationError):
        parse_chat_request(
            {"model": "m", "messages": [{"role": "tool", "content": "x"}]}
        )


def test_unknown_top_level_fields_are_ignored() -> None:
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "seed": 7,
            "response_format": {"type": "json_object"},
        }
    )

    assert parsed.model == "m"


def test_data_uri_image_part_is_parsed() -> None:
    data = base64.b64encode(b"png-bytes").decode()
    parsed = parse_chat_request(
        {
            "model": "m",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{data}"},
                        },
                    ],
                }
            ],
        }
    )

    message = parsed.messages[0]
    assert message.content == "look"
    assert message.image_parts[0].base64_data == data


def test_non_data_uri_image_raises() -> None:
    with pytest.raises(ValidationError):
        parse_chat_request(
            {
                "model": "m",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://example.com/a.png"},
                            }
                        ],
                    }
                ],
            }
        )


def test_response_to_openai_shape() -> None:
    response = CompletionResponse(
        content="done",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=3, cost=0.01),
        model="example-large-001",
    )

    body = response_to_openai(response, response_id="chatcmpl-x", created=123)

    assert body["object"] == "chat.completion"
    assert body["model"] == "example-large-001"
    choice = body["choices"][0]  # type: ignore[index]
    assert choice["message"]["content"] == "done"
    assert choice["finish_reason"] == "stop"
    assert body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
    }


def test_buffered_response_carries_reasoning_on_its_own_key() -> None:
    """A buffered client receives the channel a streaming one already gets.

    The streaming path forwards a reasoning delta as ``reasoning_content``.
    Omitted here, the same model reached without streaming appears to have
    produced no working at all, and a harness that does not stream can never
    receive it however the model answered.
    """
    response = CompletionResponse(
        content="done",
        reasoning="First I checked the spec, then the tests.",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=3, cost=0.01),
        model="example-large-001",
    )

    body = response_to_openai(response, response_id="chatcmpl-x", created=1)

    message = body["choices"][0]["message"]  # type: ignore[index]
    assert message["reasoning_content"] == "First I checked the spec, then the tests."
    # Kept apart from the answer: folding it into content would replay the
    # model's working as something it said out loud.
    assert message["content"] == "done"


def test_buffered_response_omits_the_reasoning_key_when_there_was_none() -> None:
    """A model that produced no working carries no empty channel."""
    response = CompletionResponse(
        content="done",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=3, cost=0.01),
        model="example-large-001",
    )

    body = response_to_openai(response, response_id="chatcmpl-x", created=1)

    assert "reasoning_content" not in body["choices"][0]["message"]  # type: ignore[index]


def test_response_tool_calls_serialise_arguments_as_json_string() -> None:
    response = CompletionResponse(
        tool_calls=(ToolCall(id="c1", name="run", arguments={"x": 1}),),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model="m",
    )

    body = response_to_openai(response, response_id="chatcmpl-x", created=1)

    choice = body["choices"][0]  # type: ignore[index]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "run"
    assert json.loads(call["function"]["arguments"]) == {"x": 1}


def test_stream_content_delta_maps_to_chunk() -> None:
    chunk = StreamChunk(event_type=StreamEventType.CONTENT_DELTA, content="hel")

    body = stream_chunk_to_openai(chunk, response_id="chatcmpl-x", created=1, model="m")

    assert body is not None
    assert body["object"] == "chat.completion.chunk"
    assert body["choices"][0]["delta"] == {"content": "hel"}  # type: ignore[index]


def test_stream_usage_and_done_chunks_have_no_delta() -> None:
    usage = StreamChunk(
        event_type=StreamEventType.USAGE,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
    )
    done = StreamChunk(event_type=StreamEventType.DONE)

    assert stream_chunk_to_openai(usage, response_id="x", created=1, model="m") is None
    assert stream_chunk_to_openai(done, response_id="x", created=1, model="m") is None
