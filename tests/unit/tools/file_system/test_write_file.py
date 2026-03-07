"""Tests for WriteFileTool."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from ai_company.tools.file_system.write_file import WriteFileTool


@pytest.mark.unit
class TestWriteFileExecution:
    """Execution tests."""

    async def test_create_new_file(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        result = await write_tool.execute(
            arguments={"path": "new.txt", "content": "brand new"}
        )
        assert not result.is_error
        assert "Created" in result.content
        assert result.metadata["created"] is True
        assert (workspace / "new.txt").read_text(encoding="utf-8") == "brand new"

    async def test_overwrite_existing_file(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        result = await write_tool.execute(
            arguments={"path": "hello.txt", "content": "overwritten"}
        )
        assert not result.is_error
        assert "Updated" in result.content
        assert result.metadata["created"] is False
        assert (workspace / "hello.txt").read_text(encoding="utf-8") == "overwritten"

    async def test_missing_parent_without_create_dirs(
        self, write_tool: WriteFileTool
    ) -> None:
        result = await write_tool.execute(
            arguments={"path": "no/such/dir/file.txt", "content": "x"}
        )
        assert result.is_error
        assert "Parent directory does not exist" in result.content

    async def test_create_directories(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        result = await write_tool.execute(
            arguments={
                "path": "a/b/c/deep.txt",
                "content": "deep",
                "create_directories": True,
            }
        )
        assert not result.is_error
        assert result.metadata["created"] is True
        assert (workspace / "a" / "b" / "c" / "deep.txt").read_text(
            encoding="utf-8"
        ) == "deep"

    async def test_path_traversal_blocked(self, write_tool: WriteFileTool) -> None:
        result = await write_tool.execute(
            arguments={"path": "../../escape.txt", "content": "bad"}
        )
        assert result.is_error
        assert "escapes workspace" in result.content

    async def test_write_empty_content(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        result = await write_tool.execute(
            arguments={"path": "blank.txt", "content": ""}
        )
        assert not result.is_error
        assert (workspace / "blank.txt").read_text(encoding="utf-8") == ""

    async def test_bytes_written_metadata(self, write_tool: WriteFileTool) -> None:
        result = await write_tool.execute(
            arguments={"path": "sized.txt", "content": "hello"}
        )
        assert not result.is_error
        assert result.metadata["bytes_written"] > 0
