"""Deleting a project must take its workspace tree with it.

A live run left 24 trees under the workspace root, two of them belonging to
projects deleted through the API during the run. The database cascades (the
``project_workspaces`` row carries ``ON DELETE CASCADE``) and nothing touches
disk, so the trees accumulate for ever and stay available to be recalled from
by a later plan.
"""

import shutil
import stat
from pathlib import Path

import pytest

from synthorg.core.domain_errors import DomainError
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import WorkspaceCleanupError, WorkspaceSetupError
from synthorg.engine.workspace.discard import (
    discard_project_workspace,
    force_writable_then_retry,
)
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


class TestForceWritableThenRetry:
    """The ``onexc`` handler, driven directly.

    Removing a read-only file through the tree above only reaches this handler
    where the platform refuses the unlink. On Linux the permission to unlink
    comes from the parent directory, so a read-only file there is removed
    without the handler ever being called, and the path it guards is exercised
    on one platform only.
    """

    def test_a_read_only_entry_is_made_writable_and_removed(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "pack-1.pack"
        target.write_bytes(b"PACK")
        target.chmod(0o444)
        removed: list[str] = []

        force_writable_then_retry(
            removed.append, str(target), PermissionError("read-only")
        )

        assert removed == [str(target)]
        assert target.stat().st_mode & stat.S_IWRITE

    def test_a_failure_the_write_bit_does_not_explain_is_re_raised(
        self, tmp_path: Path
    ) -> None:
        """Only a permission refusal is a candidate for the retry."""
        original = OSError("disk gone")

        with pytest.raises(OSError, match="disk gone"):
            force_writable_then_retry(
                lambda _path: None, str(tmp_path / "absent"), original
            )

    def test_an_entry_that_cannot_be_chmodded_re_raises_the_original(
        self, tmp_path: Path
    ) -> None:
        """The retry is not attempted blind: if the write bit cannot be set,
        the caller sees why the removal failed rather than why the repair
        did."""
        original = PermissionError("read-only")

        with pytest.raises(PermissionError, match="read-only"):
            force_writable_then_retry(
                lambda _path: None, str(tmp_path / "not-there"), original
            )


class TestDiscardRefusesWhatIsNotItsToRemove:
    async def test_a_traversing_project_id_is_refused(self, tmp_path: Path) -> None:
        """The id is system-generated, so this can only ever be a bug or worse."""
        outside = tmp_path / "outside"
        outside.mkdir()

        with pytest.raises(WorkspaceSetupError, match="traversal"):
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


class TestDiscardFailureIsTypedRatherThanRaw:
    async def test_a_tree_that_will_not_go_raises_a_domain_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure here runs inside a per-row bulk cascade.

        A bare ``OSError`` is not a refusal of one row: it escapes the loop
        that collects refusals, ends the whole request, and takes with it the
        record of which of the earlier rows were already irreversibly deleted.
        Typed, the row is collected and the rest of the selection completes.
        """
        path = project_workspace_dir(tmp_path, "stuck")
        path.mkdir(parents=True)

        def _refuse(*_args: object, **_kwargs: object) -> None:
            msg = "device or resource busy"
            raise OSError(msg)

        monkeypatch.setattr(shutil, "rmtree", _refuse)

        with pytest.raises(WorkspaceCleanupError):
            await discard_project_workspace(
                base_root=tmp_path, project_id=NotBlankStr("stuck")
            )

    async def test_the_failure_is_a_domain_error_a_bulk_delete_can_collect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property the bulk path depends on, asserted where it is decided."""
        path = project_workspace_dir(tmp_path, "stuck")
        path.mkdir(parents=True)

        def _refuse(*_args: object, **_kwargs: object) -> None:
            msg = "device or resource busy"
            raise OSError(msg)

        monkeypatch.setattr(shutil, "rmtree", _refuse)

        with pytest.raises(DomainError):
            await discard_project_workspace(
                base_root=tmp_path, project_id=NotBlankStr("stuck")
            )

    async def test_the_tree_is_still_there_when_removal_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refusal reports failure over files that are still present."""
        path = project_workspace_dir(tmp_path, "stuck")
        path.mkdir(parents=True)
        (path / "work.txt").write_text("real work", encoding="utf-8")

        def _refuse(*_args: object, **_kwargs: object) -> None:
            msg = "device or resource busy"
            raise OSError(msg)

        monkeypatch.setattr(shutil, "rmtree", _refuse)

        with pytest.raises(WorkspaceCleanupError):
            await discard_project_workspace(
                base_root=tmp_path, project_id=NotBlankStr("stuck")
            )

        assert (path / "work.txt").exists()
