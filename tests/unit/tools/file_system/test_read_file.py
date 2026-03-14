"""Tests for ReadFileTool."""

from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import ToolCategory
from synthorg.tools.file_system.read_file import MAX_FILE_SIZE_BYTES, ReadFileTool

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestReadFileToolProperties:
    """Tool metadata tests."""

    def test_name(self, read_tool: ReadFileTool) -> None:
        assert read_tool.name == "read_file"

    def test_category(self, read_tool: ReadFileTool) -> None:
        assert read_tool.category == ToolCategory.FILE_SYSTEM

    def test_has_schema(self, read_tool: ReadFileTool) -> None:
        schema = read_tool.parameters_schema
        assert schema is not None
        assert "path" in schema["properties"]


@pytest.mark.unit
class TestReadFileExecution:
    """Execution tests."""

    async def test_read_full_file(self, read_tool: ReadFileTool) -> None:
        result = await read_tool.execute(arguments={"path": "hello.txt"})
        assert not result.is_error
        assert "Hello, world!" in result.content
        assert result.metadata["path"] == "hello.txt"
        assert result.metadata["size_bytes"] > 0

    async def test_read_empty_file(self, read_tool: ReadFileTool) -> None:
        result = await read_tool.execute(arguments={"path": "empty.txt"})
        assert not result.is_error
        assert result.content == ""

    async def test_read_nested_file(self, read_tool: ReadFileTool) -> None:
        result = await read_tool.execute(arguments={"path": "subdir/nested.py"})
        assert not result.is_error
        assert "print('nested')" in result.content

    async def test_read_with_line_range(
        self, workspace: Path, read_tool: ReadFileTool
    ) -> None:
        (workspace / "multi.txt").write_text(
            "line1\nline2\nline3\nline4\nline5\n", encoding="utf-8"
        )
        result = await read_tool.execute(
            arguments={"path": "multi.txt", "start_line": 2, "end_line": 4}
        )
        assert not result.is_error
        assert "line2" in result.content
        assert "line4" in result.content
        assert "line1" not in result.content
        assert "line5" not in result.content

    async def test_read_start_line_only(
        self, workspace: Path, read_tool: ReadFileTool
    ) -> None:
        (workspace / "multi.txt").write_text("a\nb\nc\n", encoding="utf-8")
        result = await read_tool.execute(
            arguments={"path": "multi.txt", "start_line": 2}
        )
        assert not result.is_error
        assert result.content == "b\nc\n"

    async def test_read_end_line_only(
        self, workspace: Path, read_tool: ReadFileTool
    ) -> None:
        (workspace / "multi.txt").write_text("a\nb\nc\n", encoding="utf-8")
        result = await read_tool.execute(arguments={"path": "multi.txt", "end_line": 2})
        assert not result.is_error
        assert "a" in result.content
        assert "b" in result.content
        assert "c" not in result.content

    async def test_file_not_found(self, read_tool: ReadFileTool) -> None:
        result = await read_tool.execute(arguments={"path": "nonexistent.txt"})
        assert result.is_error
        assert "not found" in result.content.lower()

    async def test_path_traversal_blocked(self, read_tool: ReadFileTool) -> None:
        result = await read_tool.execute(arguments={"path": "../../../etc/passwd"})
        assert result.is_error
        assert "escapes workspace" in result.content

    async def test_read_directory_errors(self, read_tool: ReadFileTool) -> None:
        result = await read_tool.execute(arguments={"path": "subdir"})
        assert result.is_error
        assert "directory" in result.content.lower()

    async def test_binary_file_errors(
        self, workspace: Path, read_tool: ReadFileTool
    ) -> None:
        (workspace / "binary.bin").write_bytes(b"\x00\x01\x80\xff")
        result = await read_tool.execute(arguments={"path": "binary.bin"})
        assert result.is_error
        assert "binary" in result.content.lower()

    async def test_large_file_truncated(
        self, workspace: Path, read_tool: ReadFileTool
    ) -> None:
        big_content = "x" * (MAX_FILE_SIZE_BYTES + 1000)
        (workspace / "big.txt").write_text(big_content, encoding="utf-8")
        result = await read_tool.execute(arguments={"path": "big.txt"})
        assert not result.is_error
        assert "Truncated" in result.content
        assert len(result.content) < len(big_content)

    async def test_large_file_rejects_line_range(
        self, workspace: Path, read_tool: ReadFileTool
    ) -> None:
        """Oversized files with line ranges are rejected to prevent DoS."""
        lines = ["line\n"] * (MAX_FILE_SIZE_BYTES // 4)
        big_content = "".join(lines)
        (workspace / "big_lines.txt").write_text(big_content, encoding="utf-8")
        result = await read_tool.execute(
            arguments={"path": "big_lines.txt", "start_line": 1, "end_line": 5}
        )
        assert result.is_error
        assert "too large" in result.content.lower()

    async def test_start_line_greater_than_end_line(
        self, workspace: Path, read_tool: ReadFileTool
    ) -> None:
        """start_line > end_line returns an error."""
        (workspace / "multi.txt").write_text("a\nb\nc\n", encoding="utf-8")
        result = await read_tool.execute(
            arguments={"path": "multi.txt", "start_line": 3, "end_line": 1}
        )
        assert result.is_error
        assert "start_line" in result.content
