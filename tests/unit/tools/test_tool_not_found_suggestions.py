"""A missing tool name must say which one the caller probably meant.

A live run lost a whole task to this. An agent called ``search`` eleven
times, was handed all forty registered tool names on every attempt, never
connected it to ``memory.search``, and terminated on ``max_turns`` having
delivered nothing. The registry knew the answer and buried it in a list.
"""

from typing import override

import pytest

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.errors import ToolNotFoundError
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


class _Named(BaseTool):
    """A tool that exists only to occupy a name."""

    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            description=f"tool {name}",
            parameters_schema={"type": "object", "properties": {}},
            category=ToolCategory.ANALYTICS,
        )

    @override
    async def execute(self, **kwargs: object) -> ToolExecutionResult:
        del kwargs
        return ToolExecutionResult(content="ok")


def _registry(*names: str) -> ToolRegistry:
    return ToolRegistry([_Named(name) for name in names])


def _message_for(registry: ToolRegistry, name: str) -> str:
    with pytest.raises(ToolNotFoundError) as caught:
        registry.get(name)
    return str(caught.value)


class TestSuggestions:
    def test_a_namespaced_tool_is_suggested_for_its_bare_segment(self) -> None:
        """The exact miss that cost the run."""
        registry = _registry("memory.search", "write_file", "read_file")

        message = _message_for(registry, "search")

        assert "Did you mean" in message
        assert "memory.search" in message.split("Available tools")[0]

    def test_every_plausible_match_is_offered(self) -> None:
        registry = _registry(
            "memory.search", "search_brain", "search_knowledge", "write_file"
        )

        hint = _message_for(registry, "search").split("Available tools")[0]

        assert "memory.search" in hint
        assert "search_brain" in hint
        assert "search_knowledge" in hint
        assert "write_file" not in hint

    def test_a_typo_is_suggested_by_edit_distance(self) -> None:
        registry = _registry("write_file", "read_file")

        hint = _message_for(registry, "wrtie_file").split("Available tools")[0]

        assert "write_file" in hint

    def test_a_name_resembling_nothing_offers_no_suggestion(self) -> None:
        """A wrong guess must not be answered with a confident wrong one."""
        registry = _registry("write_file", "read_file")

        message = _message_for(registry, "zzzzzzzz")

        assert "Did you mean" not in message

    def test_the_full_list_is_still_available(self) -> None:
        """The suggestion narrows the answer; it does not withhold it."""
        registry = _registry("memory.search", "write_file")

        message = _message_for(registry, "search")

        assert "write_file" in message

    def test_an_empty_registry_still_raises_cleanly(self) -> None:
        message = _message_for(_registry(), "anything")

        assert "(none)" in message
