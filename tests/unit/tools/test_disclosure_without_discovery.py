"""A session with no discovery tool sees its whole registry.

Progressive disclosure sends only what an agent has loaded plus the three
discovery tools it loads more with. Without those three there is no way to
load anything, so an unloaded tool is not deferred, it is unreachable.

Three live runs lost every plan-review verdict to that gap. The panellist
held exactly one tool, ``submit_plan_review``, nothing was loaded, no
discovery tool existed to load it with, and the provider was called with no
tools at all. Four reviewers on four different models each answered in prose
and were recorded as having no opinion, because the only thing they could
have done was never offered.
"""

from typing import override

import pytest

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.discovery import DeferredDisclosureManager, build_discovery_tools
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


class _Submit(BaseTool):
    """A terminal submit tool, the whole registry of a review session."""

    def __init__(self) -> None:
        super().__init__(
            name="submit_plan_review",
            description="Submit the review verdict",
            parameters_schema={"type": "object", "properties": {}},
            category=ToolCategory.ANALYTICS,
        )

    @override
    async def execute(self, **kwargs: object) -> ToolExecutionResult:
        del kwargs
        return ToolExecutionResult(content="submitted")


def test_a_single_tool_session_is_offered_its_tool() -> None:
    invoker = ToolInvoker(ToolRegistry([_Submit()]))

    definitions = invoker.get_loaded_definitions(frozenset())

    assert [d.name for d in definitions] == ["submit_plan_review"], (
        "a session that holds no discovery tool cannot load anything, so "
        "deferring its only tool makes that tool uncallable"
    )


def test_a_session_with_discovery_still_defers() -> None:
    """The disclosure trade is untouched where it can actually be taken."""
    discovery = build_discovery_tools(DeferredDisclosureManager())
    invoker = ToolInvoker(ToolRegistry([_Submit(), *discovery]))

    names = {d.name for d in invoker.get_loaded_definitions(frozenset())}

    assert "submit_plan_review" not in names, (
        "with discovery tools present the agent can load it on demand"
    )
    assert "list_tools" in names


def test_a_session_with_discovery_offers_what_it_loaded() -> None:
    discovery = build_discovery_tools(DeferredDisclosureManager())
    invoker = ToolInvoker(ToolRegistry([_Submit(), *discovery]))

    names = {
        d.name
        for d in invoker.get_loaded_definitions(frozenset({"submit_plan_review"}))
    }

    assert "submit_plan_review" in names
