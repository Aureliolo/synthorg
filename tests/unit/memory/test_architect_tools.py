"""Tests for KnowledgeArchitect tools and role."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.role_catalog import get_builtin_role
from synthorg.memory.consolidation.wiki_export import WikiExporter
from synthorg.memory.tools import (
    KnowledgeArchitectBrowseWikiTool,
    KnowledgeArchitectDeleteTool,
    KnowledgeArchitectGuideTool,
    KnowledgeArchitectSearchTool,
    KnowledgeArchitectWriteTool,
)
from synthorg.persistence.memory_protocol import OrgFactRepository
from synthorg.security.autonomy.enums import ToolCategory
from tests._shared import mock_of


def _mock_org_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.query = AsyncMock(return_value=())
    backend.write = AsyncMock(return_value="fact-1")
    backend.delete = AsyncMock(return_value=True)
    return backend


class TestKnowledgeArchitectRole:
    """Tests for KnowledgeArchitect role in catalog."""

    @pytest.mark.unit
    def test_role_exists(self) -> None:
        role = get_builtin_role("Knowledge Architect")
        assert role is not None
        assert role.name == "Knowledge Architect"

    @pytest.mark.unit
    def test_role_has_tool_access(self) -> None:
        role = get_builtin_role("Knowledge Architect")
        assert role is not None
        assert "memory.guide" in role.tool_access
        assert "memory.search" in role.tool_access
        assert "memory.read" in role.tool_access
        assert "memory.write" in role.tool_access
        assert "memory.delete" in role.tool_access
        assert "memory.browse_wiki" in role.tool_access

    @pytest.mark.unit
    def test_role_is_senior(self) -> None:
        role = get_builtin_role("Knowledge Architect")
        assert role is not None
        assert role.authority_level.value == "senior"


class TestKnowledgeArchitectGuideTool:
    """Tests for memory.guide tool."""

    @pytest.mark.unit
    async def test_returns_guide_text(self) -> None:
        tool = KnowledgeArchitectGuideTool()
        result = await tool.execute(arguments={})
        assert not result.is_error
        assert "memory.guide" in result.content
        assert "memory.search" in result.content

    @pytest.mark.unit
    def test_category_is_memory(self) -> None:
        tool = KnowledgeArchitectGuideTool()
        assert tool.category == ToolCategory.MEMORY


class TestKnowledgeArchitectSearchTool:
    """Tests for memory.search tool."""

    @pytest.mark.unit
    async def test_search_empty_results(self) -> None:
        backend = _mock_org_backend()
        tool = KnowledgeArchitectSearchTool(org_backend=backend)
        result = await tool.execute(
            arguments={"query": "auth patterns"},
        )
        assert not result.is_error
        assert "No results" in result.content

    @pytest.mark.unit
    async def test_search_on_error(self) -> None:
        backend = _mock_org_backend()
        backend.query = AsyncMock(
            side_effect=RuntimeError("backend down"),
        )
        tool = KnowledgeArchitectSearchTool(org_backend=backend)
        result = await tool.execute(
            arguments={"query": "auth"},
        )
        assert result.is_error
        assert "failed" in result.content.lower()


class TestKnowledgeArchitectWriteTool:
    """Tests for memory.write tool with autonomy gating."""

    @pytest.mark.unit
    async def test_write_denied_at_full_autonomy(self) -> None:
        backend = _mock_org_backend()
        tool = KnowledgeArchitectWriteTool(
            org_backend=backend,
            agent_id="agent-1",
            autonomy_level=AutonomyLevel.FULL,
        )
        result = await tool.execute(
            arguments={
                "content": "new policy",
                "category": "procedure",
            },
        )
        assert result.is_error
        assert "denied" in result.content.lower()
        backend.write.assert_not_awaited()

    @pytest.mark.unit
    async def test_write_allowed_at_supervised(self) -> None:
        backend = _mock_org_backend()
        tool = KnowledgeArchitectWriteTool(
            org_backend=backend,
            agent_id="agent-1",
            autonomy_level=AutonomyLevel.SUPERVISED,
        )
        result = await tool.execute(
            arguments={
                "content": "new ADR",
                "category": "adr",
            },
        )
        assert not result.is_error
        assert "fact-1" in result.content

    @pytest.mark.unit
    async def test_write_denied_at_semi_without_optin(self) -> None:
        backend = _mock_org_backend()
        tool = KnowledgeArchitectWriteTool(
            org_backend=backend,
            agent_id="agent-1",
            autonomy_level=AutonomyLevel.SEMI,
        )
        result = await tool.execute(
            arguments={
                "content": "convention",
                "category": "convention",
            },
        )
        assert result.is_error
        assert "opt-in" in result.content.lower()

    @pytest.mark.unit
    async def test_write_allowed_at_semi_with_optin(self) -> None:
        backend = _mock_org_backend()
        tool = KnowledgeArchitectWriteTool(
            org_backend=backend,
            agent_id="agent-1",
            autonomy_level=AutonomyLevel.SEMI,
            architect_writes_enabled=True,
        )
        result = await tool.execute(
            arguments={
                "content": "convention",
                "category": "convention",
            },
        )
        assert not result.is_error

    @pytest.mark.unit
    async def test_write_on_backend_error(self) -> None:
        backend = _mock_org_backend()
        backend.write = AsyncMock(
            side_effect=RuntimeError("write failed"),
        )
        tool = KnowledgeArchitectWriteTool(
            org_backend=backend,
            agent_id="agent-1",
            autonomy_level=AutonomyLevel.SUPERVISED,
        )
        result = await tool.execute(
            arguments={
                "content": "test",
                "category": "procedure",
            },
        )
        assert result.is_error


class TestKnowledgeArchitectDeleteTool:
    """Tests for memory.delete tool with autonomy gating."""

    @pytest.mark.unit
    async def test_delete_denied_at_full_autonomy(self) -> None:
        backend = _mock_org_backend()
        fact_store = mock_of[OrgFactRepository](
            delete=AsyncMock(return_value=True),
        )
        tool = KnowledgeArchitectDeleteTool(
            org_backend=backend,
            fact_store=fact_store,
            agent_id="agent-1",
            autonomy_level=AutonomyLevel.FULL,
        )
        result = await tool.execute(
            arguments={"entry_id": "fact-1"},
        )
        assert result.is_error
        assert "denied" in result.content.lower()
        fact_store.delete.assert_not_awaited()

    @pytest.mark.unit
    async def test_delete_allowed_at_supervised(self) -> None:
        backend = _mock_org_backend()
        fact_store = mock_of[OrgFactRepository](
            delete=AsyncMock(return_value=True),
        )
        tool = KnowledgeArchitectDeleteTool(
            org_backend=backend,
            fact_store=fact_store,
            agent_id="agent-1",
            autonomy_level=AutonomyLevel.SUPERVISED,
        )
        result = await tool.execute(
            arguments={"entry_id": "fact-1"},
        )
        assert not result.is_error
        assert "fact-1" in result.content
        fact_store.delete.assert_awaited_once()


class TestKnowledgeArchitectBrowseWikiTool:
    """Tests for memory.browse_wiki tool."""

    @pytest.mark.unit
    async def test_no_exporter_configured(self) -> None:
        tool = KnowledgeArchitectBrowseWikiTool(
            wiki_exporter=None,
            agent_id="agent-1",
        )
        result = await tool.execute(arguments={})
        assert result.is_error
        assert "not configured" in result.content.lower()

    @pytest.mark.unit
    async def test_export_succeeds(self) -> None:
        from types import SimpleNamespace

        exporter = mock_of[WikiExporter]()
        exporter.export = AsyncMock(
            return_value=SimpleNamespace(
                raw_count=2,
                compressed_count=3,
                export_root="/data/wiki",
            ),
        )
        tool = KnowledgeArchitectBrowseWikiTool(
            wiki_exporter=exporter,
            agent_id="agent-1",
        )
        result = await tool.execute(arguments={"include_raw": True})
        assert not result.is_error
        assert "Raw entries: 2" in result.content
        assert "Compressed entries: 3" in result.content

    @pytest.mark.unit
    async def test_include_raw_false_omits_raw_count(self) -> None:
        from types import SimpleNamespace

        exporter = mock_of[WikiExporter]()
        exporter.export = AsyncMock(
            return_value=SimpleNamespace(
                raw_count=2,
                compressed_count=3,
                export_root="/data/wiki",
            ),
        )
        tool = KnowledgeArchitectBrowseWikiTool(
            wiki_exporter=exporter,
            agent_id="agent-1",
        )
        result = await tool.execute(arguments={"include_raw": False})
        assert not result.is_error
        assert "Raw entries" not in result.content
        assert "Compressed entries: 3" in result.content
