"""Shared fixtures for file system tool tests."""

from typing import TYPE_CHECKING

import pytest

from synthorg.tools.file_system.delete_file import DeleteFileTool
from synthorg.tools.file_system.edit_file import EditFileTool
from synthorg.tools.file_system.list_directory import ListDirectoryTool
from synthorg.tools.file_system.read_file import ReadFileTool
from synthorg.tools.file_system.write_file import WriteFileTool

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory with sample files."""
    (tmp_path / "hello.txt").write_text("Hello, world!\n", encoding="utf-8")
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.py").write_text("print('nested')\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def read_tool(workspace: Path) -> ReadFileTool:
    """ReadFileTool bound to the test workspace."""
    return ReadFileTool(workspace_root=workspace)


@pytest.fixture
def write_tool(workspace: Path) -> WriteFileTool:
    """WriteFileTool bound to the test workspace."""
    return WriteFileTool(workspace_root=workspace)


@pytest.fixture
def edit_tool(workspace: Path) -> EditFileTool:
    """EditFileTool bound to the test workspace."""
    return EditFileTool(workspace_root=workspace)


@pytest.fixture
def list_tool(workspace: Path) -> ListDirectoryTool:
    """ListDirectoryTool bound to the test workspace."""
    return ListDirectoryTool(workspace_root=workspace)


@pytest.fixture
def delete_tool(workspace: Path) -> DeleteFileTool:
    """DeleteFileTool bound to the test workspace."""
    return DeleteFileTool(workspace_root=workspace)
