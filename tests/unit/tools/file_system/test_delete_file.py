"""Tests for DeleteFileTool."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.tools.file_system.delete_file import DeleteFileTool

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestDeleteFileProperties:
    """Tool metadata tests."""

    def test_require_elevated(self, delete_tool: DeleteFileTool) -> None:
        assert delete_tool.require_elevated is True


@pytest.mark.unit
class TestDeleteFileExecution:
    """Execution tests."""

    async def test_delete_existing_file(
        self, workspace: Path, delete_tool: DeleteFileTool
    ) -> None:
        assert (workspace / "hello.txt").exists()
        result = await delete_tool.execute(arguments={"path": "hello.txt"})
        assert not result.is_error
        assert "Deleted" in result.content
        assert not (workspace / "hello.txt").exists()
        assert result.metadata["path"] == "hello.txt"
        assert result.metadata["size_bytes"] > 0

    async def test_delete_nonexistent_file(self, delete_tool: DeleteFileTool) -> None:
        result = await delete_tool.execute(arguments={"path": "does_not_exist.txt"})
        assert result.is_error
        assert "not found" in result.content.lower()

    async def test_delete_directory_rejected(self, delete_tool: DeleteFileTool) -> None:
        result = await delete_tool.execute(arguments={"path": "subdir"})
        assert result.is_error
        assert "Cannot delete directory" in result.content

    async def test_path_traversal_blocked(self, delete_tool: DeleteFileTool) -> None:
        result = await delete_tool.execute(arguments={"path": "../../../etc/passwd"})
        assert result.is_error
        assert "escapes workspace" in result.content

    async def test_delete_nested_file(
        self, workspace: Path, delete_tool: DeleteFileTool
    ) -> None:
        assert (workspace / "subdir" / "nested.py").exists()
        result = await delete_tool.execute(arguments={"path": "subdir/nested.py"})
        assert not result.is_error
        assert not (workspace / "subdir" / "nested.py").exists()

    async def test_delete_empty_file(
        self, workspace: Path, delete_tool: DeleteFileTool
    ) -> None:
        result = await delete_tool.execute(arguments={"path": "empty.txt"})
        assert not result.is_error
        assert not (workspace / "empty.txt").exists()
        assert result.metadata["size_bytes"] == 0

    async def test_delete_permission_error(
        self,
        workspace: Path,
        delete_tool: DeleteFileTool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PermissionError during deletion is handled gracefully."""
        from synthorg.tools.file_system import delete_file as mod

        def _fake_delete_sync(resolved: Path) -> int:
            raise PermissionError

        monkeypatch.setattr(mod, "_delete_sync", _fake_delete_sync)
        result = await delete_tool.execute(
            arguments={"path": "hello.txt"},
        )
        assert result.is_error
        assert "Permission denied" in result.content

    async def test_delete_os_error(
        self,
        workspace: Path,
        delete_tool: DeleteFileTool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic OSError during deletion is handled gracefully."""
        from synthorg.tools.file_system import delete_file as mod

        def _fake_delete_sync(resolved: Path) -> int:
            raise OSError

        monkeypatch.setattr(mod, "_delete_sync", _fake_delete_sync)
        result = await delete_tool.execute(
            arguments={"path": "hello.txt"},
        )
        assert result.is_error
        assert "OS error" in result.content
