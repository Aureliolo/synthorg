"""Tests for prompt-caching cache_control breakpoint placement."""

import pytest

from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_cache import apply_cache_control
from synthorg.providers.drivers.litellm_kwargs import _AcompletionKwargs

pytestmark = pytest.mark.unit


def _caps(*, supports_prompt_caching: bool) -> ModelCapabilities:
    return ModelCapabilities(
        model_id="example-large-001",
        provider="example-provider",
        max_context_tokens=200_000,
        max_output_tokens=8192,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        supports_prompt_caching=supports_prompt_caching,
    )


def _kwargs(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None = None,
) -> _AcompletionKwargs:
    kwargs: _AcompletionKwargs = {
        "model": "example-provider/example-large-001",
        "messages": messages,
    }
    if tools is not None:
        kwargs["tools"] = tools
    return kwargs


def _apply(kwargs: _AcompletionKwargs, *, supports_prompt_caching: bool) -> None:
    apply_cache_control(
        kwargs,
        capabilities=_caps(supports_prompt_caching=supports_prompt_caching),
        provider_name="example-provider",
        model_id="example-large-001",
    )


def _last_block_cache_control(content: object) -> object:
    assert isinstance(content, list)
    last = content[-1]
    assert isinstance(last, dict)
    return last.get("cache_control")


class TestApplyCacheControl:
    def test_noop_for_non_caching_model(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        _apply(_kwargs(messages), supports_prompt_caching=False)

        assert messages[0]["content"] == "sys"
        assert messages[1]["content"] == "hi"

    def test_marks_last_system_and_rolling_tail(self) -> None:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "two"},
        ]
        _apply(_kwargs(messages), supports_prompt_caching=True)

        assert _last_block_cache_control(messages[0]["content"]) == {
            "type": "ephemeral"
        }
        # Rolling breakpoint on the final (last user) message.
        assert _last_block_cache_control(messages[-1]["content"]) == {
            "type": "ephemeral"
        }
        # An untouched middle message keeps its plain string content.
        assert messages[1]["content"] == "one"

    def test_marks_last_of_stacked_system_messages(self) -> None:
        """With an org + per-task system block, only the last is marked.

        Marking the first would place the cache breakpoint before the stable
        prefix ends, silently breaking the prefix match with no hard error.
        """
        messages: list[dict[str, object]] = [
            {"role": "system", "content": "org policy"},
            {"role": "system", "content": "task instructions"},
            {"role": "user", "content": "go"},
        ]
        _apply(_kwargs(messages), supports_prompt_caching=True)

        assert messages[0]["content"] == "org policy"
        assert _last_block_cache_control(messages[1]["content"]) == {
            "type": "ephemeral"
        }

    def test_marks_last_tool(self) -> None:
        messages: list[dict[str, object]] = [{"role": "user", "content": "hi"}]
        tools: list[dict[str, object]] = [
            {"type": "function", "function": {"name": "a"}},
            {"type": "function", "function": {"name": "b"}},
        ]
        _apply(_kwargs(messages, tools), supports_prompt_caching=True)

        assert "cache_control" not in tools[0]
        assert tools[1]["cache_control"] == {"type": "ephemeral"}

    def test_string_content_rewritten_to_block(self) -> None:
        messages: list[dict[str, object]] = [{"role": "user", "content": "hello"}]
        _apply(_kwargs(messages), supports_prompt_caching=True)

        assert messages[0]["content"] == [
            {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
        ]

    def test_skips_toolcall_only_assistant_for_rolling(self) -> None:
        """A content-less assistant message is skipped; the prior user is marked."""
        messages: list[dict[str, object]] = [
            {"role": "user", "content": "run it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function"}],
            },
        ]
        _apply(_kwargs(messages), supports_prompt_caching=True)

        # The rolling breakpoint falls back to the user message.
        assert _last_block_cache_control(messages[0]["content"]) == {
            "type": "ephemeral"
        }
        # The tool-call-only assistant message is untouched.
        assert messages[1]["content"] is None

    def test_existing_block_list_gets_marker_on_last_block(self) -> None:
        messages: list[dict[str, object]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        _apply(_kwargs(messages), supports_prompt_caching=True)

        content = messages[0]["content"]
        assert isinstance(content, list)
        assert content[-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in content[0]
