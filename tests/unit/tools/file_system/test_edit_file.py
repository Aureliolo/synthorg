"""Tests for EditFileTool."""

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.tools.file_system.edit_file import EditFileTool


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
        # Verify no file content snippet is leaked.
        assert "Hello" not in result.content

    async def test_multiple_occurrences_without_replace_all_errors(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """An ambiguous (non-unique) match is rejected, not silently first-edited."""
        (workspace / "dups.txt").write_text("aaa bbb aaa", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "dups.txt",
                "old_text": "aaa",
                "new_text": "ccc",
            }
        )
        assert result.is_error
        assert "not unique" in result.content
        assert "replace_all" in result.content
        # Nothing is written for a rejected edit.
        content = (workspace / "dups.txt").read_text(encoding="utf-8")
        assert content == "aaa bbb aaa"
        assert result.metadata["occurrences_replaced"] == 0

    async def test_replace_all_replaces_every_occurrence(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        (workspace / "dups.txt").write_text("aaa bbb aaa", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "dups.txt",
                "old_text": "aaa",
                "new_text": "ccc",
                "replace_all": True,
            }
        )
        assert not result.is_error
        content = (workspace / "dups.txt").read_text(encoding="utf-8")
        assert content == "ccc bbb ccc"
        assert result.metadata["occurrences_found"] == 2
        assert result.metadata["occurrences_replaced"] == 2

    async def test_multi_hunk_atomic_apply(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        (workspace / "cfg.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "cfg.txt",
                "edits": [
                    {"old_text": "alpha", "new_text": "ALPHA"},
                    {"old_text": "gamma", "new_text": "GAMMA"},
                ],
            }
        )
        assert not result.is_error
        assert "Applied 2 edits" in result.content
        content = (workspace / "cfg.txt").read_text(encoding="utf-8")
        assert content == "ALPHA\nbeta\nGAMMA\n"
        assert result.metadata["edits_applied"] == 2
        assert result.metadata["occurrences_replaced"] == 2

    async def test_multi_hunk_rolls_back_on_missing_hunk(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """If any hunk cannot apply, the whole edit is rejected atomically."""
        (workspace / "cfg.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "cfg.txt",
                "edits": [
                    {"old_text": "alpha", "new_text": "ALPHA"},
                    {"old_text": "missing", "new_text": "X"},
                ],
            }
        )
        assert result.is_error
        assert "Edit 2 of 2" in result.content
        assert "not found" in result.content.lower()
        # First hunk must not have been written.
        content = (workspace / "cfg.txt").read_text(encoding="utf-8")
        assert content == "alpha\nbeta\n"

    async def test_multi_hunk_sequential_sees_prior_result(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """A later hunk edits the text a prior hunk produced."""
        (workspace / "seq.txt").write_text("one", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "seq.txt",
                "edits": [
                    {"old_text": "one", "new_text": "two"},
                    {"old_text": "two", "new_text": "three"},
                ],
            }
        )
        assert not result.is_error
        content = (workspace / "seq.txt").read_text(encoding="utf-8")
        assert content == "three"

    async def test_single_and_edits_mutually_exclusive(
        self, edit_tool: EditFileTool
    ) -> None:
        with pytest.raises(ValidationError):
            await edit_tool.execute(
                arguments={
                    "path": "hello.txt",
                    "old_text": "world",
                    "new_text": "universe",
                    "edits": [{"old_text": "a", "new_text": "b"}],
                }
            )

    async def test_neither_single_nor_edits_rejected(
        self, edit_tool: EditFileTool
    ) -> None:
        with pytest.raises(ValidationError):
            await edit_tool.execute(arguments={"path": "hello.txt"})

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

    async def test_edit_directory_errors(self, edit_tool: EditFileTool) -> None:
        result = await edit_tool.execute(
            arguments={
                "path": "subdir",
                "old_text": "a",
                "new_text": "b",
            }
        )
        assert result.is_error
        assert "directory" in result.content.lower()

    async def test_empty_old_text_rejected(self, edit_tool: EditFileTool) -> None:
        """Empty old_text is rejected at the typed boundary."""
        # ``EditFileArgs.old_text`` has ``min_length=1``; an empty value
        # raises in ``parse_typed`` before any edit is attempted.
        with pytest.raises(ValidationError):
            await edit_tool.execute(
                arguments={
                    "path": "hello.txt",
                    "old_text": "",
                    "new_text": "injected",
                }
            )

    async def test_edit_large_file_rejected(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """Files exceeding the size guard are rejected."""
        from synthorg.tools.file_system.edit_file import MAX_EDIT_FILE_SIZE_BYTES

        big = "x" * (MAX_EDIT_FILE_SIZE_BYTES + 100)
        (workspace / "huge.txt").write_text(big, encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "huge.txt",
                "old_text": "x",
                "new_text": "y",
            }
        )
        assert result.is_error
        assert "too large" in result.content.lower()
