"""What ``map_response`` says about tool calls that did not survive.

Extraction drops one call at a time, so a turn asking for two tools can
deliver one and lose the other. ``dropped_tool_calls`` is the only record of
the loss, and it has to mean the same thing here as on the streaming path:
the two answer the same question for the loop's correction, and a field with
two meanings is one the next consumer reads wrong.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.providers._cost import _cache_tokens
from synthorg.providers.drivers.litellm_response import map_response
from synthorg.providers.models import CompletionResponse
from tests.unit.providers.drivers.conftest import (
    make_mock_response,
    make_mock_tool_call,
)

pytestmark = pytest.mark.unit

_MODEL = ProviderModelConfig(
    id="test-model-001",
    alias="medium",
    cost_per_1k_input=0.003,
    cost_per_1k_output=0.015,
    max_context=200_000,
)


def _mapped(
    tool_calls: list[MagicMock] | None, *, finish: str = "tool_calls"
) -> CompletionResponse:
    """Map a response carrying *tool_calls*.

    Returns:
        The mapped :class:`CompletionResponse`.
    """
    response = make_mock_response(
        content=None,
        tool_calls=tool_calls,
        finish_reason=finish,
    )
    return map_response(response, _MODEL, provider_name="test-provider")


class TestDroppedToolCalls:
    def test_a_turn_whose_only_call_is_malformed_reports_the_drop(self) -> None:
        mapped = _mapped([make_mock_tool_call(arguments="{not json")])

        assert mapped.tool_calls == ()
        assert mapped.dropped_tool_calls is True

    def test_a_surviving_sibling_does_not_hide_the_drop(self) -> None:
        """The case an empty-``tool_calls`` test cannot reach.

        Reading the loss off an empty result reports this turn as having lost
        nothing, while one of the two tools the model asked for never ran.
        """
        mapped = _mapped(
            [
                make_mock_tool_call(call_id="call_ok", name="read_file"),
                make_mock_tool_call(call_id="call_bad", arguments="{not json"),
            ]
        )

        assert [c.id for c in mapped.tool_calls] == ["call_ok"]
        assert mapped.dropped_tool_calls is True

    def test_calls_that_all_survive_report_no_drop(self) -> None:
        mapped = _mapped(
            [
                make_mock_tool_call(call_id="call_a", name="read_file"),
                make_mock_tool_call(call_id="call_b", name="shell_command"),
            ]
        )

        assert len(mapped.tool_calls) == 2
        assert mapped.dropped_tool_calls is False

    def test_a_turn_that_asked_for_no_tool_reports_no_drop(self) -> None:
        """Nothing arrived, so nothing was lost: the other correction's shape."""
        response = make_mock_response(content="done", tool_calls=None)

        mapped = map_response(response, _MODEL, provider_name="test-provider")

        assert mapped.dropped_tool_calls is False
        assert mapped.finish_reason is FinishReason.STOP

    def test_calls_arriving_as_a_one_shot_iterable_are_read_once(self) -> None:
        """Extraction and the count are two reads of one sequence.

        A provider object that hands back an iterator is spent by the first
        read, so taking the loss from a second pass over it would either
        measure nothing or raise on an object with no length. Every other
        case here passes a list, which cannot tell the two apart.
        """
        calls = [
            make_mock_tool_call(call_id="call_ok", name="read_file"),
            make_mock_tool_call(call_id="call_bad", arguments="{not json"),
        ]
        response = make_mock_response(
            content=None, tool_calls=None, finish_reason="tool_calls"
        )
        response.choices[0].message.tool_calls = iter(calls)

        mapped = map_response(response, _MODEL, provider_name="test-provider")

        assert [c.id for c in mapped.tool_calls] == ["call_ok"]
        assert mapped.dropped_tool_calls is True


class TestCacheTokens:
    """The cached-prefix counts are read as COUNTS, never collapsed to a flag.

    A provider that publishes no cache data reads as zero, which contributes
    nothing to the cached share rather than reading as every call missing.
    """

    def test_openai_shaped_cached_tokens_are_the_read_count(self) -> None:
        assert _cache_tokens(
            SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=40))
        ) == (40, 0)

    def test_flat_cache_read_tokens_are_the_fallback_shape(self) -> None:
        assert _cache_tokens(SimpleNamespace(cache_read_input_tokens=25)) == (25, 0)

    def test_cache_creation_tokens_are_the_write_count(self) -> None:
        assert _cache_tokens(
            SimpleNamespace(cache_read_input_tokens=25, cache_creation_input_tokens=7)
        ) == (25, 7)

    def test_no_cache_shape_reads_as_zero(self) -> None:
        assert _cache_tokens(SimpleNamespace(prompt_tokens=100)) == (0, 0)

    def test_none_usage_object_reads_as_zero(self) -> None:
        assert _cache_tokens(None) == (0, 0)

    def test_a_corrupt_count_becomes_a_missing_one(self) -> None:
        corrupt = SimpleNamespace(
            cache_read_input_tokens=-3, cache_creation_input_tokens=True
        )
        assert _cache_tokens(corrupt) == (0, 0)

    def test_map_response_carries_the_counts_on_usage(self) -> None:
        response = make_mock_response(content="done", tool_calls=None)
        response.usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            cache_creation_input_tokens=12,
        )

        mapped = map_response(response, _MODEL, provider_name="test-provider")

        assert mapped.usage.cache_read_input_tokens == 40
        assert mapped.usage.cache_write_input_tokens == 12

    def test_map_response_reports_zero_when_nothing_was_reported(self) -> None:
        response = make_mock_response(content="done", tool_calls=None)
        response.usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)

        mapped = map_response(response, _MODEL, provider_name="test-provider")

        assert mapped.usage.cache_read_input_tokens == 0
        assert mapped.usage.cache_write_input_tokens == 0
