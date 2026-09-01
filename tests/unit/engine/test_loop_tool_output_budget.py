"""The tool-output ceiling: head and tail kept, the elision stated.

Asserted on strings so the arithmetic is visible, and on a resolver double
so the live read and its fallback are both exercised.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.engine.loop_tool_output_budget import (
    DEFAULT_TOOL_OUTPUT_MAX_CHARS,
    abbreviate_tool_output,
    resolve_tool_output_max_chars,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestAbbreviateToolOutput:
    def test_content_within_the_ceiling_is_untouched(self) -> None:
        assert abbreviate_tool_output("short", max_chars=100) == ("short", 0)

    def test_zero_disables_the_ceiling(self) -> None:
        content = "x" * 10_000
        assert abbreviate_tool_output(content, max_chars=0) == (content, 0)

    def test_keeps_the_head_and_the_tail_within_the_ceiling(self) -> None:
        content = "".join(f"line {index}\n" for index in range(2_000))

        kept, elided = abbreviate_tool_output(content, max_chars=1_000)

        assert len(kept) <= 1_000
        assert kept.startswith("line 0\n")
        assert kept.endswith("line 1999\n")
        head, marker_and_tail = kept.split("\n[...", maxsplit=1)
        tail = marker_and_tail.split("...]\n", maxsplit=1)[1]
        assert elided == len(content) - len(head) - len(tail)

    def test_the_marker_states_how_much_was_dropped(self) -> None:
        content = "a" * 500

        kept, elided = abbreviate_tool_output(content, max_chars=200)

        assert f"{elided} of 500 characters elided" in kept
        assert "engine.tool_output_max_chars" in kept

    def test_the_head_takes_the_larger_share(self) -> None:
        content = "H" * 500 + "T" * 500

        kept, _elided = abbreviate_tool_output(content, max_chars=300)

        assert kept.count("H") > kept.count("T")
        assert kept.count("T") > 0


class TestResolveToolOutputMaxChars:
    async def test_no_resolver_answers_the_registered_default(self) -> None:
        assert (
            await resolve_tool_output_max_chars(None) == DEFAULT_TOOL_OUTPUT_MAX_CHARS
        )

    async def test_reads_the_live_setting(self) -> None:
        resolver = mock_of[ConfigResolverProtocol](get_int=AsyncMock(return_value=512))

        assert await resolve_tool_output_max_chars(resolver) == 512
        resolver.get_int.assert_awaited_once_with("engine", "tool_output_max_chars")

    async def test_a_failed_read_falls_back_to_the_default(self) -> None:
        resolver = mock_of[ConfigResolverProtocol](
            get_int=AsyncMock(side_effect=OSError("down"))
        )

        assert (
            await resolve_tool_output_max_chars(resolver)
            == DEFAULT_TOOL_OUTPUT_MAX_CHARS
        )
