"""Tests for typed file-system tool argument models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.file_system._args import (
    DeleteFileArgs,
    EditFileArgs,
    ListDirectoryArgs,
    ReadFileArgs,
    WriteFileArgs,
)

pytestmark = pytest.mark.unit


class TestReadFileArgs:
    def test_minimal_construction(self) -> None:
        args = ReadFileArgs(path="src/main.py")
        assert args.path == "src/main.py"
        assert args.start_line is None
        assert args.end_line is None

    def test_with_line_range(self) -> None:
        args = ReadFileArgs(path="x.py", start_line=10, end_line=20)
        assert args.start_line == 10
        assert args.end_line == 20

    def test_blank_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs(path="   ")

    def test_zero_start_line_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs(path="x", start_line=0)

    def test_negative_end_line_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs(path="x", end_line=-1)

    def test_reversed_line_range_rejected(self) -> None:
        """Cross-field rule: ``start_line`` must be <= ``end_line``.

        Without this case the per-field bounds tests above would pass
        even if the cross-field validator regressed silently.
        """
        with pytest.raises(ValidationError):
            ReadFileArgs(path="x", start_line=20, end_line=10)

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs.model_validate(
                {"path": "x", "smuggled": "field"},
            )


class TestWriteFileArgs:
    def test_minimal_construction(self) -> None:
        args = WriteFileArgs(path="x.txt", content="hello")
        assert args.create_directories is False

    def test_empty_content_allowed(self) -> None:
        args = WriteFileArgs(path="x.txt", content="")
        assert args.content == ""

    def test_create_directories_flag(self) -> None:
        args = WriteFileArgs(
            path="a/b/x.txt",
            content="",
            create_directories=True,
        )
        assert args.create_directories is True

    def test_missing_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WriteFileArgs.model_validate({"path": "x.txt"})


class TestEditFileArgs:
    def test_construction(self) -> None:
        args = EditFileArgs(path="x.py", old_text="foo", new_text="bar")
        assert args.path == "x.py"

    def test_empty_new_text_allowed(self) -> None:
        """Empty new_text is the documented way to delete matched text."""
        args = EditFileArgs(path="x.py", old_text="foo", new_text="")
        assert args.new_text == ""

    def test_empty_old_text_rejected(self) -> None:
        """old_text=`` would match nothing meaningful."""
        with pytest.raises(ValidationError):
            EditFileArgs(path="x.py", old_text="", new_text="bar")

    def test_replace_all_defaults_false(self) -> None:
        args = EditFileArgs(path="x.py", old_text="foo", new_text="bar")
        assert args.replace_all is False

    def test_batch_edits_construction(self) -> None:
        args = EditFileArgs(
            path="x.py",
            edits=(
                {"old_text": "a", "new_text": "b"},  # type: ignore[arg-type]
                {"old_text": "c", "new_text": "d", "replace_all": True},
            ),
        )
        hunks = args.normalized_hunks()
        assert len(hunks) == 2
        assert hunks[1].replace_all is True

    def test_single_edit_normalizes_to_one_hunk(self) -> None:
        args = EditFileArgs(
            path="x.py", old_text="foo", new_text="bar", replace_all=True
        )
        hunks = args.normalized_hunks()
        assert len(hunks) == 1
        assert hunks[0].old_text == "foo"
        assert hunks[0].replace_all is True

    def test_both_modes_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EditFileArgs(
                path="x.py",
                old_text="foo",
                new_text="bar",
                edits=({"old_text": "a", "new_text": "b"},),  # type: ignore[arg-type]
            )

    def test_neither_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EditFileArgs(path="x.py")

    def test_old_text_without_new_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EditFileArgs(path="x.py", old_text="foo")


class TestDeleteFileArgs:
    def test_construction(self) -> None:
        args = DeleteFileArgs(path="tmp.txt")
        assert args.path == "tmp.txt"

    def test_blank_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DeleteFileArgs(path="")


class TestListDirectoryArgs:
    def test_default_path_is_workspace_root(self) -> None:
        args = ListDirectoryArgs()
        assert args.path == "."
        assert args.pattern is None
        assert args.recursive is False

    def test_with_pattern_and_recursive(self) -> None:
        args = ListDirectoryArgs(
            path="src",
            pattern="*.py",
            recursive=True,
        )
        assert args.pattern == "*.py"
        assert args.recursive is True

    def test_blank_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ListDirectoryArgs(pattern="   ")
