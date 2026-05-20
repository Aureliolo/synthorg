"""Unit tests for :class:`WriteLivingDocTool` and :class:`SearchLivingDocsTool`.

These tests build a real :class:`DocsService` wired against a fake
docs repo, the inmemory memory backend, and a real
:class:`ProjectWorkspaceService` (driven by the embedded git backend
in a tmp dir). The tool's ``execute`` path is exercised end-to-end so
the agent-facing contract is covered without a full simulation
harness; the ALSO-exercised facade fan-out lives in the integration
suite (`tests/integration/docs_engine/`).
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.enums import DocType, GitBackendType
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.factory import build_docs_service
from synthorg.docs_engine.tool_factory import DocsToolFactory
from synthorg.engine.workspace.git_backend import (
    GitBackendConfig,
    GitBackendDeps,
    build_git_backend,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.tools.docs import SearchLivingDocsTool, WriteLivingDocTool
from tests._shared import FakeClock
from tests.unit.api.fakes import FakeDocsRepository

pytestmark = pytest.mark.integration


class _InMemoryWorkspaceRepo:
    def __init__(self) -> None:
        self._rows: dict[str, ProjectWorkspace] = {}

    async def save(self, entity: ProjectWorkspace) -> None:
        self._rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectWorkspace | None:
        return self._rows.get(entity_id)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectWorkspace, ...]:
        rows = sorted(self._rows.values(), key=lambda r: r.project_id)
        return tuple(rows[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None


async def _build_tools(tmp_path: Path):
    config = GitBackendConfig(kind=GitBackendType.EMBEDDED)
    git_backend = build_git_backend(
        config,
        GitBackendDeps(workspace_base_root=tmp_path, clock=FakeClock()),
    )
    workspace_service = ProjectWorkspaceService(
        base_root=tmp_path,
        repo=_InMemoryWorkspaceRepo(),
        git_backend=git_backend,
        config=config,
        clock=FakeClock(),
    )
    backend = InMemoryBackend()
    await backend.connect()
    runtime = build_docs_service(
        repo=FakeDocsRepository(),
        workspace_service=workspace_service,
        git_backend=git_backend,
        memory_backend=backend,
        clock=FakeClock(start=datetime(2026, 5, 20, tzinfo=UTC)),
    )
    factory = DocsToolFactory(docs_service=runtime.docs_service)
    tools = factory.build_tools(
        project_id=NotBlankStr("proj-1"),
        author_agent_id=NotBlankStr("agent_alice"),
    )
    return tools, backend


class TestWriteLivingDocTool:
    async def test_writes_doc_via_execute(self, tmp_path: Path) -> None:
        tools, backend = await _build_tools(tmp_path)
        try:
            write_tool, _ = tools
            assert isinstance(write_tool, WriteLivingDocTool)
            result = await write_tool.execute(
                arguments={
                    "title": "Q2 Status",
                    "doc_type": "status_report",
                    "body": (
                        {
                            "block_kind": "heading",
                            "level": 2,
                            "text": "Summary",
                        },
                        {
                            "block_kind": "prose",
                            "text": "Checkout improved by 5%.",
                        },
                        {
                            "block_kind": "decision",
                            "decision": "Hold rewrite",
                            "rationale": "A/B still ramping",
                        },
                    ),
                },
            )
            assert result.is_error is False
            assert result.metadata["doc_type"] == "status_report"
            assert "q2-status" in result.metadata["slug"]
        finally:
            await backend.disconnect()

    async def test_rejects_invalid_block_shape(self, tmp_path: Path) -> None:
        tools, backend = await _build_tools(tmp_path)
        try:
            write_tool, _ = tools
            result = await write_tool.execute(
                arguments={
                    "title": "Bad",
                    "doc_type": "status_report",
                    "body": (
                        {"block_kind": "heading"},  # missing level + text
                    ),
                },
            )
            assert result.is_error is True
        finally:
            await backend.disconnect()


class TestSearchLivingDocsTool:
    async def test_finds_written_doc(self, tmp_path: Path) -> None:
        tools, backend = await _build_tools(tmp_path)
        try:
            write_tool, search_tool = tools
            assert isinstance(search_tool, SearchLivingDocsTool)
            await write_tool.execute(
                arguments={
                    "title": "Checkout fix",
                    "doc_type": "status_report",
                    "body": (
                        {
                            "block_kind": "prose",
                            "text": "Resolved race condition in checkout.",
                        },
                    ),
                },
            )
            result = await search_tool.execute(
                arguments={"query": "checkout"},
            )
            assert result.is_error is False
            assert result.metadata["hit_count"] >= 1
            assert "checkout-fix" in result.content
        finally:
            await backend.disconnect()

    async def test_doc_type_filter(self, tmp_path: Path) -> None:
        tools, backend = await _build_tools(tmp_path)
        try:
            write_tool, search_tool = tools
            await write_tool.execute(
                arguments={
                    "title": "Status alpha",
                    "doc_type": "status_report",
                    "body": ({"block_kind": "prose", "text": "status alpha body"},),
                },
            )
            await write_tool.execute(
                arguments={
                    "title": "Deliverable alpha",
                    "doc_type": "deliverable",
                    "body": (
                        {"block_kind": "prose", "text": "deliverable alpha body"},
                    ),
                },
            )
            result = await search_tool.execute(
                arguments={
                    "query": "alpha",
                    "doc_types": (DocType.DELIVERABLE.value,),
                },
            )
            assert result.is_error is False
            hits = result.metadata["hits"]
            assert all(h["doc_type"] == "deliverable" for h in hits)
        finally:
            await backend.disconnect()
