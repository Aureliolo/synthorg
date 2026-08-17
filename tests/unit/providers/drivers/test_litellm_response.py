"""What ``map_response`` says about tool calls that did not survive.

Extraction drops one call at a time, so a turn asking for two tools can
deliver one and lose the other. ``dropped_tool_calls`` is the only record of
the loss, and it has to mean the same thing here as on the streaming path:
the two answer the same question for the loop's correction, and a field with
two meanings is one the next consumer reads wrong.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.completion_enums import FinishReason
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
