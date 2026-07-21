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

    async def test_noop_hunk_with_nonunique_text_skips_uniqueness_check(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """A no-op hunk (old==new) is skipped before the uniqueness check.

        The no-op's ``old_text`` appears many times, which would otherwise be
        rejected as non-unique; skipping it must neither reject the batch nor
        inflate the occurrence counts, and the real hunk still applies.
        """
        (workspace / "batch.txt").write_text("dup dup dup real", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "batch.txt",
                "edits": [
                    {"old_text": "dup", "new_text": "dup"},
                    {"old_text": "real", "new_text": "DONE"},
                ],
            }
        )
        assert not result.is_error
        content = (workspace / "batch.txt").read_text(encoding="utf-8")
        assert content == "dup dup dup DONE"
        assert result.metadata["occurrences_found"] == 1
        assert result.metadata["occurrences_replaced"] == 1

    async def test_replace_all_hunk_aggregates_across_batch(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """A replace_all hunk hitting 3 matches aggregates with a plain hunk."""
        (workspace / "agg.txt").write_text("x x x once", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "agg.txt",
                "edits": [
                    {"old_text": "x", "new_text": "Y", "replace_all": True},
                    {"old_text": "once", "new_text": "twice"},
                ],
            }
        )
        assert not result.is_error
        content = (workspace / "agg.txt").read_text(encoding="utf-8")
        assert content == "Y Y Y twice"
        # 3 from the replace_all hunk + 1 from the plain hunk.
        assert result.metadata["occurrences_found"] == 4
        assert result.metadata["occurrences_replaced"] == 4

    async def test_not_unique_reports_actual_match_count(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """The non-unique rejection reports the true match count beyond two."""
        (workspace / "trip.txt").write_text("aa bb aa cc aa", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "trip.txt",
                "old_text": "aa",
                "new_text": "zz",
            }
        )
        assert result.is_error
        assert "3 matches" in result.content

    async def test_length_changing_hunk_does_not_disturb_later_hunk(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """A hunk that lengthens the text leaves a later hunk's match intact."""
        (workspace / "len.txt").write_text("short then tail", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "len.txt",
                "edits": [
                    {"old_text": "short", "new_text": "a much longer replacement"},
                    {"old_text": "tail", "new_text": "END"},
                ],
            }
        )
        assert not result.is_error
        content = (workspace / "len.txt").read_text(encoding="utf-8")
        assert content == "a much longer replacement then END"

    async def test_hunk_creates_match_a_later_hunk_consumes(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """A later hunk may target text an earlier hunk produced."""
        (workspace / "chain.txt").write_text("seed", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "chain.txt",
                "edits": [
                    {"old_text": "seed", "new_text": "grown"},
                    {"old_text": "grown", "new_text": "harvested"},
                ],
            }
        )
        assert not result.is_error
        content = (workspace / "chain.txt").read_text(encoding="utf-8")
        assert content == "harvested"

    async def test_empty_new_text_deletion_inside_batch(
        self, workspace: Path, edit_tool: EditFileTool
    ) -> None:
        """An empty new_text deletes matched text when used as a batch hunk."""
        (workspace / "del.txt").write_text("keep DROP keep", encoding="utf-8")
        result = await edit_tool.execute(
            arguments={
                "path": "del.txt",
                "edits": [
                    {"old_text": " DROP", "new_text": ""},
                    {"old_text": "keep keep", "new_text": "kept"},
                ],
            }
        )
        assert not result.is_error
        content = (workspace / "del.txt").read_text(encoding="utf-8")
        assert content == "kept"

    async def test_edit_of_already_violating_file_is_not_blocked(
        self,
        workspace: Path,
        edit_tool: EditFileTool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The guard blocks only a violation the edit introduces.

        With the output policy stubbed so a marker string counts as a hard
        violation, introducing it into a clean file is blocked, while editing
        elsewhere in a file that already carries it is allowed: the
        ``before.blocked`` short-circuit stops an already-imperfect file from
        being frozen against every further edit.
        """
        from types import SimpleNamespace

        from synthorg.engine import output_style

        def fake_evaluate(text: str, ctx: object) -> object | None:
            del ctx
            if "VIOLATION" in text:
                return SimpleNamespace(blocked=True, summary="hard-rule violation")
            return None

        monkeypatch.setattr(output_style, "evaluate_output_policy", fake_evaluate)

        (workspace / "clean.py").write_text("a = 1\nold = 2\n", encoding="utf-8")
        introduced = await edit_tool.execute(
            arguments={
                "path": "clean.py",
                "old_text": "a = 1",
                "new_text": "a = 1  # VIOLATION",
            }
        )
        assert introduced.is_error
        assert (workspace / "clean.py").read_text(
            encoding="utf-8"
        ) == "a = 1\nold = 2\n"

        (workspace / "viol.py").write_text(
            "a = 1  # VIOLATION\nold = 2\n", encoding="utf-8"
        )
        result = await edit_tool.execute(
            arguments={
                "path": "viol.py",
                "old_text": "old = 2",
                "new_text": "renamed = 2",
            }
        )
        assert not result.is_error
        content = (workspace / "viol.py").read_text(encoding="utf-8")
        assert "renamed = 2" in content
        assert "VIOLATION" in content
