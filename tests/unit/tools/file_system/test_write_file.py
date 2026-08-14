"""Tests for WriteFileTool."""

import os
import stat
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from synthorg.core.workspace_sharing import WORKSPACE_DIR_MODE, WORKSPACE_FILE_MODE
from tests._shared import JsonDict

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.tools.file_system.write_file import WriteFileTool


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
        assert cast(JsonDict, result.metadata)["bytes_written"] > 0

    async def test_write_to_directory_errors(self, write_tool: WriteFileTool) -> None:
        """Writing to a path that is a directory returns an error."""
        result = await write_tool.execute(arguments={"path": "subdir", "content": "x"})
        assert result.is_error
        assert "directory" in result.content.lower()

    async def test_write_too_large_content_rejected(
        self, write_tool: WriteFileTool
    ) -> None:
        """Content exceeding MAX_WRITE_SIZE_BYTES is rejected."""
        from synthorg.tools.file_system.write_file import MAX_WRITE_SIZE_BYTES

        big = "x" * (MAX_WRITE_SIZE_BYTES + 100)
        result = await write_tool.execute(
            arguments={"path": "huge.txt", "content": big}
        )
        assert result.is_error
        assert "too large" in result.content.lower()


@pytest.mark.unit
@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX permission bits; chmod does not carry group bits on Windows.",
)
class TestWriteFileSharesWithTheSandbox:
    """A file an agent writes must be reachable by the sandbox that tests it.

    The sandbox runs as its own uid and joins the backend's gid, so the
    delivered mode is the whole mechanism: without the group bits a test
    runner opening the source gets ``EACCES`` and the build/test oracle can
    never see a passing run.
    """

    async def test_a_created_file_is_group_readable(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        """``mkstemp`` creates owner-only, so the mode must be stated."""
        result = await write_tool.execute(
            arguments={"path": "fresh.txt", "content": "x"}
        )
        assert not result.is_error
        mode = stat.S_IMODE((workspace / "fresh.txt").stat().st_mode)
        assert mode == WORKSPACE_FILE_MODE

    async def test_an_overwritten_script_keeps_its_execute_bit(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        """Overwriting must not narrow what the target already grants."""
        target = workspace / "run.sh"
        target.write_text("echo old\n", encoding="utf-8")
        target.chmod(0o750)
        result = await write_tool.execute(
            arguments={"path": "run.sh", "content": "echo new\n"}
        )
        assert not result.is_error
        assert stat.S_IMODE(target.stat().st_mode) == 0o750

    async def test_an_owner_only_file_is_repaired_on_overwrite(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        """A file written before this contract existed is widened, not left dark."""
        target = workspace / "legacy.txt"
        target.write_text("old\n", encoding="utf-8")
        target.chmod(0o600)
        result = await write_tool.execute(
            arguments={"path": "legacy.txt", "content": "new\n"}
        )
        assert not result.is_error
        assert stat.S_IMODE(target.stat().st_mode) == WORKSPACE_FILE_MODE

    async def test_created_directories_are_group_writable_and_setgid(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        """Every created component, not just the last one.

        An atomic replace writes into the parent directory, so a
        non-group-writable component anywhere on the path stops the sandbox
        rewriting the file it holds. Setgid keeps what the sandbox creates
        under the shared group.
        """
        result = await write_tool.execute(
            arguments={
                "path": "x/y/z/deep.txt",
                "content": "deep",
                "create_directories": True,
            }
        )
        assert not result.is_error
        for part in ("x", "x/y", "x/y/z"):
            mode = stat.S_IMODE((workspace / part).stat().st_mode)
            assert mode == WORKSPACE_DIR_MODE, part

    async def test_an_existing_directory_is_left_alone(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        """Only directories this write creates are modified.

        Re-moding a directory the operator or a checkout already placed would
        make an unrelated write a permissions change nobody asked for.
        """
        existing = workspace / "kept"
        existing.mkdir()
        existing.chmod(0o700)
        result = await write_tool.execute(
            arguments={
                "path": "kept/inner/file.txt",
                "content": "x",
                "create_directories": True,
            }
        )
        assert not result.is_error
        assert stat.S_IMODE(existing.stat().st_mode) == 0o700
        assert stat.S_IMODE((existing / "inner").stat().st_mode) == WORKSPACE_DIR_MODE


# ── _perform_write error handling ────────────────────────────────


@pytest.mark.unit
class TestWriteFileErrors:
    async def test_is_a_directory_error_from_write_sync(
        self, write_tool: WriteFileTool
    ) -> None:
        """IsADirectoryError from _write_sync is caught."""
        with patch(
            "synthorg.tools.file_system.write_file._write_sync",
            side_effect=IsADirectoryError("is a directory"),
        ):
            result = await write_tool.execute(
                arguments={"path": "hello.txt", "content": "x"},
            )
        assert result.is_error
        assert "directory" in result.content.lower()

    async def test_oserror_from_write_sync(self, write_tool: WriteFileTool) -> None:
        """Generic OSError from _write_sync is caught."""
        with patch(
            "synthorg.tools.file_system.write_file._write_sync",
            side_effect=OSError("disk full"),
        ):
            result = await write_tool.execute(
                arguments={"path": "hello.txt", "content": "x"},
            )
        assert result.is_error
        assert "OS error" in result.content

    async def test_temp_file_cleanup_on_failure(
        self, workspace: Path, write_tool: WriteFileTool
    ) -> None:
        """Temp file is removed when atomic replace fails inside _write_sync."""
        target = workspace / "fail_target.txt"
        target.write_text("original", encoding="utf-8")

        with patch("os.fsync", side_effect=OSError("disk full")):
            result = await write_tool.execute(
                arguments={"path": "fail_target.txt", "content": "new"},
            )

        assert result.is_error
        # No leftover .tmp files in the workspace
        assert not any(
            name.endswith(".tmp")
            for name in os.listdir(str(workspace))  # noqa: PTH208
        )
        # Original file unchanged (write never completed)
        assert target.read_text(encoding="utf-8") == "original"
