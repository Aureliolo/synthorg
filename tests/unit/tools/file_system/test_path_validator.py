"""Tests for PathValidator."""

import os
import sys
from typing import TYPE_CHECKING

import pytest

from synthorg.tools.file_system._path_validator import PathValidator

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout(30)

_SYMLINK_SUPPORTED = not (sys.platform == "win32" and not os.environ.get("CI"))


@pytest.mark.unit
class TestPathValidatorInit:
    """Constructor validation tests."""

    def test_valid_workspace(self, tmp_path: Path) -> None:
        pv = PathValidator(tmp_path)
        assert pv.workspace_root == tmp_path.resolve()

    def test_nonexistent_workspace_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not an existing directory"):
            PathValidator(tmp_path / "nope")

    def test_file_as_workspace_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="not an existing directory"):
            PathValidator(f)


@pytest.mark.unit
class TestValidate:
    """Path validation tests."""

    def test_relative_path(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        pv = PathValidator(tmp_path)
        result = pv.validate("a.txt")
        assert result == (tmp_path / "a.txt").resolve()

    def test_nested_path(self, tmp_path: Path) -> None:
        sub = tmp_path / "d"
        sub.mkdir()
        (sub / "b.txt").write_text("x", encoding="utf-8")
        pv = PathValidator(tmp_path)
        result = pv.validate("d/b.txt")
        assert result == (sub / "b.txt").resolve()

    def test_traversal_rejected(self, tmp_path: Path) -> None:
        pv = PathValidator(tmp_path)
        with pytest.raises(ValueError, match="escapes workspace"):
            pv.validate("../etc/passwd")

    def test_absolute_outside_rejected(self, tmp_path: Path) -> None:
        pv = PathValidator(tmp_path)
        with pytest.raises(ValueError, match="Absolute paths not allowed"):
            pv.validate("/etc/passwd")

    def test_dot_path(self, tmp_path: Path) -> None:
        pv = PathValidator(tmp_path)
        result = pv.validate(".")
        assert result == tmp_path.resolve()


@pytest.mark.unit
class TestValidateParentExists:
    """Parent-existence validation tests."""

    def test_existing_parent(self, tmp_path: Path) -> None:
        pv = PathValidator(tmp_path)
        result = pv.validate_parent_exists("new_file.txt")
        assert result.parent == tmp_path.resolve()

    def test_missing_parent_raises(self, tmp_path: Path) -> None:
        pv = PathValidator(tmp_path)
        with pytest.raises(ValueError, match="Parent directory does not exist"):
            pv.validate_parent_exists("no/such/dir/file.txt")

    def test_traversal_still_rejected(self, tmp_path: Path) -> None:
        pv = PathValidator(tmp_path)
        with pytest.raises(ValueError, match="escapes workspace"):
            pv.validate_parent_exists("../../escape.txt")


@pytest.mark.unit
class TestSymlinkHandling:
    """Symlink-specific validation tests."""

    @pytest.mark.skipif(
        not _SYMLINK_SUPPORTED,
        reason="Symlinks require privileges on Windows outside CI",
    )
    def test_symlink_outside_workspace_rejected(self, tmp_path: Path) -> None:
        """A symlink pointing outside the workspace must be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        link = workspace / "escape_link"
        link.symlink_to(outside / "secret.txt")

        pv = PathValidator(workspace)
        with pytest.raises(ValueError, match="escapes workspace"):
            pv.validate("escape_link")

    @pytest.mark.skipif(
        not _SYMLINK_SUPPORTED,
        reason="Symlinks require privileges on Windows outside CI",
    )
    def test_symlink_inside_workspace_allowed(self, tmp_path: Path) -> None:
        """A symlink pointing within the workspace should be allowed."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        real_file = workspace / "real.txt"
        real_file.write_text("ok", encoding="utf-8")
        link = workspace / "link.txt"
        link.symlink_to(real_file)

        pv = PathValidator(workspace)
        result = pv.validate("link.txt")
        assert result == real_file.resolve()
