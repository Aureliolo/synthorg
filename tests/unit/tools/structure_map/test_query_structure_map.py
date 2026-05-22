"""Unit tests for ``QueryStructureMapTool``."""

from datetime import UTC, datetime

import pytest

from synthorg.core.codebase_structure_map import (
    CodebaseStructureMap,
    Dependency,
    DependencyScope,
    Ecosystem,
    EntryPoint,
    EntryPointKind,
    Module,
    ModuleKind,
)
from synthorg.core.types import NotBlankStr
from synthorg.tools.structure_map.query_structure_map import QueryStructureMapTool
from tests.unit.api.fakes import FakeCodebaseStructureMapRepository

pytestmark = pytest.mark.unit


def _map() -> CodebaseStructureMap:
    return CodebaseStructureMap(
        project_id=NotBlankStr("proj-1"),
        source_ref=NotBlankStr("/src/legacy"),
        modules=(
            Module(path="alpha", language=Ecosystem.PYTHON, kind=ModuleKind.PACKAGE),
            Module(path="beta", language=Ecosystem.PYTHON, kind=ModuleKind.PACKAGE),
        ),
        entry_points=(EntryPoint(path="cli.py", kind=EntryPointKind.MAIN_MODULE),),
        dependencies=(
            Dependency(
                name="httpx",
                ecosystem=Ecosystem.PYTHON,
                scope=DependencyScope.RUNTIME,
                version_spec=">=0.27",
            ),
        ),
        scanned_at=datetime(2026, 5, 22, tzinfo=UTC),
        content_hash="a" * 64,
    )


async def _repo_with_map() -> FakeCodebaseStructureMapRepository:
    repo = FakeCodebaseStructureMapRepository()
    await repo.save(_map())
    return repo


class TestQueryStructureMapTool:
    async def test_lists_modules(self) -> None:
        repo = await _repo_with_map()
        tool = QueryStructureMapTool(
            repository=repo,
            project_id=NotBlankStr("proj-1"),
        )

        result = await tool.execute(arguments={"facet": "modules"})

        assert result.is_error is False
        assert "alpha" in result.content
        assert "beta" in result.content
        # Imported content is wrapped as untrusted before reaching the agent.
        assert "<task-data>" in result.content

    async def test_name_filter_narrows_results(self) -> None:
        repo = await _repo_with_map()
        tool = QueryStructureMapTool(
            repository=repo,
            project_id=NotBlankStr("proj-1"),
        )

        result = await tool.execute(
            arguments={"facet": "modules", "name_filter": "alpha"}
        )

        assert "alpha" in result.content
        assert "beta" not in result.content

    async def test_dependencies_facet(self) -> None:
        repo = await _repo_with_map()
        tool = QueryStructureMapTool(
            repository=repo,
            project_id=NotBlankStr("proj-1"),
        )

        result = await tool.execute(arguments={"facet": "dependencies"})

        assert "httpx" in result.content
        assert ">=0.27" in result.content

    async def test_missing_map_reports_absence(self) -> None:
        tool = QueryStructureMapTool(
            repository=FakeCodebaseStructureMapRepository(),
            project_id=NotBlankStr("ghost"),
        )

        result = await tool.execute(arguments={"facet": "modules"})

        assert result.is_error is False
        assert "No codebase structure map" in result.content

    async def test_invalid_facet_is_error(self) -> None:
        repo = await _repo_with_map()
        tool = QueryStructureMapTool(
            repository=repo,
            project_id=NotBlankStr("proj-1"),
        )

        result = await tool.execute(arguments={"facet": "nonsense"})

        assert result.is_error is True
