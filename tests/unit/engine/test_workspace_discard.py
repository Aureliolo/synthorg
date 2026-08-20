"""Deleting a project must take its workspace tree with it.

A live run left 24 trees under the workspace root, two of them belonging to
projects deleted through the API during the run. The database cascades (the
``project_workspaces`` row carries ``ON DELETE CASCADE``) and nothing touches
disk, so the trees accumulate for ever and stay available to be recalled from
by a later plan.
"""

from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.discard import discard_project_workspace
from synthorg.engine.workspace.paths import project_workspace_dir

pytestmark = pytest.mark.unit


def _managed_tree(root: Path, project_id: str) -> Path:
    tree = project_workspace_dir(root, project_id)
    (tree / ".git").mkdir(parents=True)
    (tree / "server.js").write_text("// work", encoding="utf-8")
    return tree


class TestDiscardRemovesTheManagedTree:
    async def test_a_provisioned_tree_is_removed(self, tmp_path: Path) -> None:
        tree = _managed_tree(tmp_path, "proj-1")

        removed = await discard_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj-1")
        )

        assert removed is True
        assert not tree.exists()

    async def test_a_project_that_never_provisioned_one_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        removed = await discard_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("never-ran")
        )

        assert removed is False

    async def test_a_sibling_project_is_untouched(self, tmp_path: Path) -> None:
        """The trees sit side by side; only the named one may go."""
        keep = _managed_tree(tmp_path, "proj-keep")
        _managed_tree(tmp_path, "proj-go")

        await discard_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj-go")
        )

        assert (keep / "server.js").exists()

    async def test_a_read_only_git_object_does_not_strand_the_tree(
        self, tmp_path: Path
    ) -> None:
        """Git writes pack files read-only, and Windows refuses to unlink them."""
        tree = _managed_tree(tmp_path, "proj-packed")
        pack = tree / ".git" / "objects" / "pack" / "pack-1.pack"
        pack.parent.mkdir(parents=True)
        pack.write_bytes(b"PACK")
        pack.chmod(0o444)

        removed = await discard_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj-packed")
        )

        assert removed is True
        assert not tree.exists()


class TestDiscardRefusesWhatIsNotItsToRemove:
    async def test_a_traversing_project_id_is_refused(self, tmp_path: Path) -> None:
        """The id is system-generated, so this can only ever be a bug or worse."""
        outside = tmp_path / "outside"
        outside.mkdir()

        with pytest.raises(Exception, match="traversal"):
            await discard_project_workspace(
                base_root=tmp_path / "projects-root",
                project_id=NotBlankStr("../../outside"),
            )

        assert outside.exists()

    async def test_a_file_where_the_tree_should_be_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """Only a directory is a workspace; anything else is somebody else's."""
        path = project_workspace_dir(tmp_path, "proj-file")
        path.parent.mkdir(parents=True)
        path.write_text("not a workspace", encoding="utf-8")

        removed = await discard_project_workspace(
            base_root=tmp_path, project_id=NotBlankStr("proj-file")
        )

        assert removed is False
        assert path.exists()
