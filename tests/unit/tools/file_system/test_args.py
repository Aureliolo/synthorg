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


class TestReadFileArgs:
    @pytest.mark.unit
    def test_minimal_construction(self) -> None:
        args = ReadFileArgs(path="src/main.py")
        assert args.path == "src/main.py"
        assert args.start_line is None
        assert args.end_line is None

    @pytest.mark.unit
    def test_with_line_range(self) -> None:
        args = ReadFileArgs(path="x.py", start_line=10, end_line=20)
        assert args.start_line == 10
        assert args.end_line == 20

    @pytest.mark.unit
    def test_blank_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs(path="   ")

    @pytest.mark.unit
    def test_zero_start_line_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs(path="x", start_line=0)

    @pytest.mark.unit
    def test_negative_end_line_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs(path="x", end_line=-1)

    @pytest.mark.unit
    def test_reversed_line_range_rejected(self) -> None:
        """Cross-field rule: ``start_line`` must be <= ``end_line``.

        Without this case the per-field bounds tests above would pass
        even if the cross-field validator regressed silently.
        """
        with pytest.raises(ValidationError):
            ReadFileArgs(path="x", start_line=20, end_line=10)

    @pytest.mark.unit
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReadFileArgs.model_validate(
                {"path": "x", "smuggled": "field"},
            )


class TestWriteFileArgs:
    @pytest.mark.unit
    def test_minimal_construction(self) -> None:
        args = WriteFileArgs(path="x.txt", content="hello")
        assert args.create_directories is False

    @pytest.mark.unit
    def test_empty_content_allowed(self) -> None:
        args = WriteFileArgs(path="x.txt", content="")
        assert args.content == ""

    @pytest.mark.unit
    def test_create_directories_flag(self) -> None:
        args = WriteFileArgs(
            path="a/b/x.txt",
            content="",
            create_directories=True,
        )
        assert args.create_directories is True

    @pytest.mark.unit
    def test_missing_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WriteFileArgs.model_validate({"path": "x.txt"})


class TestEditFileArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = EditFileArgs(path="x.py", old_text="foo", new_text="bar")
        assert args.path == "x.py"

    @pytest.mark.unit
    def test_empty_new_text_allowed(self) -> None:
        """Empty new_text is the documented way to delete matched text."""
        args = EditFileArgs(path="x.py", old_text="foo", new_text="")
        assert args.new_text == ""

    @pytest.mark.unit
    def test_empty_old_text_rejected(self) -> None:
        """old_text=`` would match nothing meaningful."""
        with pytest.raises(ValidationError):
            EditFileArgs(path="x.py", old_text="", new_text="bar")


class TestDeleteFileArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = DeleteFileArgs(path="tmp.txt")
        assert args.path == "tmp.txt"

    @pytest.mark.unit
    def test_blank_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DeleteFileArgs(path="")


class TestListDirectoryArgs:
    @pytest.mark.unit
    def test_default_path_is_workspace_root(self) -> None:
        args = ListDirectoryArgs()
        assert args.path == "."
        assert args.pattern is None
        assert args.recursive is False

    @pytest.mark.unit
    def test_with_pattern_and_recursive(self) -> None:
        args = ListDirectoryArgs(
            path="src",
            pattern="*.py",
            recursive=True,
        )
        assert args.pattern == "*.py"
        assert args.recursive is True

    @pytest.mark.unit
    def test_blank_pattern_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ListDirectoryArgs(pattern="   ")
