"""Tests for EditFileTool."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from ai_company.tools.file_system.edit_file import EditFileTool


@pytest.mark.unit
class TestEditFileExecution:
    """Execution tests."""

    async def test_replace_text(self, workspace: Path, edit_tool: EditFileTool) -> None:
        result = await edit_tool.execute(
            arguments={
                "path": "hello.txt",
                "old_text": "world",
                "new_text": "universe",
            }
        )
        assert not result.is_error
        assert "Replaced 1 occurrence" in result.content
        content = (workspace / "hello.txt").read_text(encoding="utf-8")
        assert "universe" in content
        assert "world" not in content

    async def test_delete_text_with_empty_new(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        result = await edit_tool.execute(
            arguments={
                "path": "hello.txt",
                "old_text": ", world",
                "new_text": "",
            }
        )
        assert not result.is_error
        content = (workspace / "hello.txt").read_text(encoding="utf-8")
        assert content == "Hello!\n"

    async def test_text_not_found(self, edit_tool: EditFileTool) -> None:
        result = await edit_tool.execute(
            arguments={
                "path": "hello.txt",
                "old_text": "nonexistent string",
                "new_text": "replacement",
            }
        )
        assert result.is_error
        assert "Text not found" in result.content
        assert result.metadata["occurrences_found"] == 0

    async def test_multiple_occurrences_replaces_first(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        (workspace / "dups.txt").write_text("aaa bbb aaa", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "dups.txt",
                "old_text": "aaa",
                "new_text": "ccc",
            }
        )
        assert not result.is_error
        assert "2 total occurrences" in result.content
        content = (workspace / "dups.txt").read_text(encoding="utf-8")
        assert content == "ccc bbb aaa"
        assert result.metadata["occurrences_found"] == 2
        assert result.metadata["occurrences_replaced"] == 1

    async def test_identical_old_new_text(self, edit_tool: EditFileTool) -> None:
        result = await edit_tool.execute(
            arguments={
                "path": "hello.txt",
                "old_text": "same",
                "new_text": "same",
            }
        )
        assert not result.is_error
        assert "No change needed" in result.content

    async def test_file_not_found(self, edit_tool: EditFileTool) -> None:
        result = await edit_tool.execute(
            arguments={
                "path": "nope.txt",
                "old_text": "a",
                "new_text": "b",
            }
        )
        assert result.is_error
        assert "not found" in result.content.lower()

    async def test_path_traversal_blocked(self, edit_tool: EditFileTool) -> None:
        result = await edit_tool.execute(
            arguments={
                "path": "../../../etc/hosts",
                "old_text": "a",
                "new_text": "b",
            }
        )
        assert result.is_error
        assert "escapes workspace" in result.content

    async def test_binary_file_errors(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        (workspace / "bin.dat").write_bytes(b"\x00\x01\x80\xff")
        result = await edit_tool.execute(
            arguments={
                "path": "bin.dat",
                "old_text": "x",
                "new_text": "y",
            }
        )
        assert result.is_error
        assert "binary" in result.content.lower()

    async def test_edit_preserves_other_content(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        (workspace / "multi.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "multi.txt",
                "old_text": "line2",
                "new_text": "LINE_TWO",
            }
        )
        assert not result.is_error
        content = (workspace / "multi.txt").read_text(encoding="utf-8")
        assert content == "line1\nLINE_TWO\nline3\n"
