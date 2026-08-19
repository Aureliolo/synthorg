"""Tests for the concrete planning-tool provider (web research grant)."""

from pathlib import Path

import pytest

from synthorg.engine.decomposition.agent_session import _READ_ONLY_ACTION_TYPES
from synthorg.engine.decomposition.planning_tool_provider import PlanningToolProvider
from synthorg.engine.decomposition.tool_provider import DecompositionToolProvider
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.tools.file_system._base_fs_tool import BaseFileSystemTool
from synthorg.tools.web.web_search import SearchFilters, SearchResult

pytestmark = pytest.mark.unit

_PROJECT = "99e06e87-04f5-559b-a6b4-a3b518461069"
_OTHER_PROJECT = "5fce631f-662b-5083-abf3-328c7dde99b5"


class _StubSearch:
    """Minimal WebSearchProvider for grant tests."""

    async def search(
        self,
        query: str,
        max_results: int = 10,
        filters: SearchFilters | None = None,
    ) -> list[SearchResult]:
        del query, max_results, filters
        return []

    def unsupported_filters(self, filters: SearchFilters | None) -> tuple[str, ...]:
        """This stub applies whatever it is asked for."""
        del filters
        return ()


def test_satisfies_protocol() -> None:
    provider = PlanningToolProvider(search_provider=_StubSearch())
    assert isinstance(provider, DecompositionToolProvider)


def test_no_tools_when_search_unconfigured() -> None:
    provider = PlanningToolProvider(search_provider=None)
    assert provider.build_tools(owner_id="o", project_id="p") == ()


def test_grants_web_search_when_configured() -> None:
    provider = PlanningToolProvider(search_provider=_StubSearch())
    tools = provider.build_tools(owner_id="o", project_id=None)
    assert [t.name for t in tools] == ["web_search"]


def test_granted_web_search_survives_read_only_filter() -> None:
    """The grant is pointless if the planning session then drops it.

    web_search must carry a read-only action type so the agent-session
    strategy's read-only filter keeps it.
    """
    provider = PlanningToolProvider(search_provider=_StubSearch())
    (tool,) = provider.build_tools(owner_id="o", project_id=None)
    assert tool.action_type in _READ_ONLY_ACTION_TYPES


def test_grants_workspace_reads_for_the_project_being_planned(tmp_path: Path) -> None:
    """A planner that cannot read its own workspace cannot check a premise.

    Recall spans every project the org has run, so a recalled claim that a
    file exists is unfalsifiable unless the session can look. The plan that
    prompted this asserted an existing engine, renderer and backend for a
    project whose workspace had never been provisioned, and scoped every item
    as integration of code nobody had written.
    """
    workspace = project_workspace_dir(tmp_path, _PROJECT)
    workspace.mkdir(parents=True)
    provider = PlanningToolProvider(search_provider=None, workspace_root=tmp_path)
    names = [t.name for t in provider.build_tools(owner_id="o", project_id=_PROJECT)]
    assert names == ["read_file", "list_directory"]


def test_workspace_reads_are_scoped_to_that_project(tmp_path: Path) -> None:
    """The grant must not reach a sibling project's workspace.

    Reading another project's tree is how the unscoped recall problem would
    reappear one layer down: the planner would confirm a file that exists,
    but in the wrong project.
    """
    mine = project_workspace_dir(tmp_path, _PROJECT)
    mine.mkdir(parents=True)
    project_workspace_dir(tmp_path, _OTHER_PROJECT).mkdir(parents=True)
    provider = PlanningToolProvider(search_provider=None, workspace_root=tmp_path)
    tools = provider.build_tools(owner_id="o", project_id=_PROJECT)
    assert tools
    for tool in tools:
        assert isinstance(tool, BaseFileSystemTool)
        assert tool.workspace_root == mine.resolve()


def test_no_workspace_reads_when_the_project_has_none(tmp_path: Path) -> None:
    """An unprovisioned workspace grants nothing rather than raising.

    A project's workspace is created on first dispatch, so planning routinely
    runs before it exists. That is the honest answer (nothing to read) and it
    must not abort the planning session.
    """
    provider = PlanningToolProvider(search_provider=None, workspace_root=tmp_path)
    assert provider.build_tools(owner_id="o", project_id=_PROJECT) == ()


def test_no_workspace_reads_without_a_project() -> None:
    """A brief with no project binding has no workspace to scope to."""
    provider = PlanningToolProvider(search_provider=None, workspace_root=Path("/ws"))
    assert provider.build_tools(owner_id="o", project_id=None) == ()


def test_granted_workspace_reads_survive_read_only_filter(tmp_path: Path) -> None:
    """The workspace grant is pointless if the session then drops it."""
    project_workspace_dir(tmp_path, _PROJECT).mkdir(parents=True)
    provider = PlanningToolProvider(search_provider=None, workspace_root=tmp_path)
    tools = provider.build_tools(owner_id="o", project_id=_PROJECT)
    assert tools
    assert all(t.action_type in _READ_ONLY_ACTION_TYPES for t in tools)
