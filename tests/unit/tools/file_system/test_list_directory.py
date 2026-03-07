"""Tests for ListDirectoryTool."""

from typing import TYPE_CHECKING

import pytest

from ai_company.tools.file_system.list_directory import (
    MAX_ENTRIES,
    ListDirectoryTool,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
class TestListDirectoryExecution:
    """Execution tests."""

    async def test_list_workspace_root(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={})
        assert not result.is_error
        assert "[FILE]" in result.content
        assert "hello.txt" in result.content
        assert "[DIR]" in result.content
        assert "subdir" in result.content

    async def test_list_explicit_dot(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": "."})
        assert not result.is_error
        assert "hello.txt" in result.content

    async def test_list_subdirectory(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": "subdir"})
        assert not result.is_error
        assert "nested.py" in result.content
        assert result.metadata["files"] >= 1

    async def test_glob_pattern(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": ".", "pattern": "*.txt"})
        assert not result.is_error
        assert "hello.txt" in result.content
        # subdir should not appear when filtering by *.txt
        assert "subdir" not in result.content

    async def test_recursive_listing(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": ".", "recursive": True})
        assert not result.is_error
        assert "nested.py" in result.content

    async def test_empty_directory(
        self, workspace: Path, list_tool: ListDirectoryTool
    ) -> None:
        (workspace / "empty_dir").mkdir()
        result = await list_tool.execute(arguments={"path": "empty_dir"})
        assert not result.is_error
        assert "empty" in result.content.lower()

    async def test_not_a_directory(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": "hello.txt"})
        assert result.is_error
        assert "Not a directory" in result.content

    async def test_path_traversal_blocked(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": "../../.."})
        assert result.is_error
        assert "escapes workspace" in result.content

    async def test_metadata_counts(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": "."})
        assert not result.is_error
        assert result.metadata["directories"] >= 1
        assert result.metadata["files"] >= 1
        assert result.metadata["total_entries"] == (
            result.metadata["directories"] + result.metadata["files"]
        )

    async def test_file_shows_size(self, list_tool: ListDirectoryTool) -> None:
        result = await list_tool.execute(arguments={"path": "."})
        assert not result.is_error
        assert "bytes)" in result.content

    async def test_large_directory_truncation(self, workspace: Path) -> None:
        big_dir = workspace / "big"
        big_dir.mkdir()
        for i in range(MAX_ENTRIES + 10):
            (big_dir / f"file_{i:05d}.txt").write_text("x", encoding="utf-8")
        tool = ListDirectoryTool(workspace_root=workspace)
        result = await tool.execute(arguments={"path": "big"})
        assert not result.is_error
        assert "Truncated" in result.content
