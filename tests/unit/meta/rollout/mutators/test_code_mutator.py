"""WorkspaceCodeMutator atomic-writes inside the workspace root."""

from pathlib import Path

import pytest

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.rollout.mutators import WorkspaceCodeMutator

pytestmark = pytest.mark.unit


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace directory with one nested subdirectory pre-created."""
    (tmp_path / "subdir").mkdir()
    return tmp_path


class TestWorkspaceCodeMutator:
    """The CodeMutator surface for the rollback executor."""

    async def test_revert_file_writes_content(self, workspace: Path) -> None:
        mutator = WorkspaceCodeMutator(workspace_root=workspace)

        await mutator.revert_file(path="src.py", content="print('hi')\n")

        target = workspace / "src.py"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "print('hi')\n"

    async def test_revert_file_overwrites_existing(
        self,
        workspace: Path,
    ) -> None:
        (workspace / "existing.py").write_text("old content\n", encoding="utf-8")
        mutator = WorkspaceCodeMutator(workspace_root=workspace)

        await mutator.revert_file(
            path="existing.py",
            content="restored content\n",
        )

        assert (workspace / "existing.py").read_text(encoding="utf-8") == (
            "restored content\n"
        )

    async def test_revert_file_writes_nested_path(
        self,
        workspace: Path,
    ) -> None:
        mutator = WorkspaceCodeMutator(workspace_root=workspace)

        await mutator.revert_file(
            path="subdir/nested.py",
            content="nested file\n",
        )

        assert (workspace / "subdir" / "nested.py").read_text(
            encoding="utf-8",
        ) == "nested file\n"

    async def test_revert_file_blocks_path_traversal(
        self,
        workspace: Path,
    ) -> None:
        mutator = WorkspaceCodeMutator(workspace_root=workspace)

        with pytest.raises(
            RollbackMutationDeniedError,
            match="invalid workspace path",
        ):
            await mutator.revert_file(
                path="../escape.py",
                content="should not be written",
            )

        assert not (workspace.parent / "escape.py").exists()

    async def test_revert_file_no_temp_files_left_behind(
        self,
        workspace: Path,
    ) -> None:
        """Successful writes leave no .rollback temp files in the dir."""
        mutator = WorkspaceCodeMutator(workspace_root=workspace)

        await mutator.revert_file(path="clean.py", content="ok\n")

        entries = list(workspace.iterdir())  # noqa: ASYNC240
        leftover = [p for p in entries if p.suffix == ".rollback"]
        assert leftover == []
