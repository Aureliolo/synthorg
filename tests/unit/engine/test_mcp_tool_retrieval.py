"""Retrieval over the scoped MCP surface: narrows, never widens, and ranks.

Asserted on hand-built tool definitions so the ranking is legible: a tool
named for the brief's domain outranks one that merely mentions it, a term
every tool carries decides nothing, and the kept set keeps the scoper's
order.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.mcp_tool_retrieval import rank_tools, tokenize
from synthorg.meta.mcp.registry import MCPToolDef

pytestmark = pytest.mark.unit


def _tool(name: str, description: str) -> MCPToolDef:
    return MCPToolDef(
        name=NotBlankStr(name),
        description=NotBlankStr(description),
        parameters={},
        capability=NotBlankStr("tasks:read"),
        handler_key=NotBlankStr(name),
    )


_SURFACE = (
    _tool("synthorg_agents_list", "List every agent on the roster"),
    _tool("synthorg_tasks_list", "List tasks with optional status filters"),
    _tool("synthorg_tasks_create", "Create a task in a project"),
    _tool("synthorg_budget_summary", "Spend against the budget, per agent"),
    _tool("synthorg_projects_list", "List projects and their task counts"),
)


class TestTokenize:
    def test_lower_cases_splits_and_strips_plurals(self) -> None:
        assert tokenize("List Tasks, then create a task!") == {
            "list",
            "task",
            "then",
            "create",
        }

    def test_drops_terms_too_short_to_mean_anything(self) -> None:
        assert tokenize("a of to") == frozenset()


class TestRankTools:
    def test_keeps_the_whole_surface_when_it_fits(self) -> None:
        assert rank_tools(_SURFACE, query="anything", top_k=10) == _SURFACE

    def test_zero_keeps_the_whole_surface(self) -> None:
        assert rank_tools(_SURFACE, query="tasks", top_k=0) == _SURFACE

    def test_a_query_with_no_terms_keeps_the_whole_surface(self) -> None:
        assert rank_tools(_SURFACE, query="a", top_k=2) == _SURFACE

    def test_keeps_the_tools_the_brief_is_about(self) -> None:
        kept = rank_tools(_SURFACE, query="Create the tasks for sprint 3", top_k=2)

        assert [tool.name for tool in kept] == [
            "synthorg_tasks_list",
            "synthorg_tasks_create",
        ]

    def test_a_name_match_outranks_a_description_mention(self) -> None:
        kept = rank_tools(_SURFACE, query="tasks", top_k=2)

        # ``projects_list`` mentions tasks in its description only.
        assert {tool.name for tool in kept} == {
            "synthorg_tasks_list",
            "synthorg_tasks_create",
        }

    def test_kept_tools_keep_the_scopers_order(self) -> None:
        kept = rank_tools(_SURFACE, query="budget agents", top_k=2)

        assert [tool.name for tool in kept] == [
            "synthorg_agents_list",
            "synthorg_budget_summary",
        ]

    def test_a_term_every_tool_carries_decides_nothing(self) -> None:
        # Every name starts with ``synthorg``; ranking on it alone falls back
        # to the scoper's order rather than favouring any tool.
        kept = rank_tools(_SURFACE, query="synthorg", top_k=2)

        assert kept == _SURFACE[:2]

    def test_never_adds_a_tool_the_scoper_did_not_admit(self) -> None:
        scoped = _SURFACE[:2]

        kept = rank_tools(scoped, query="budget", top_k=1)

        assert {tool.name for tool in kept} <= {tool.name for tool in scoped}
