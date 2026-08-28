"""Unit tests for the workspace fingerprint.

This is the evidence three delivery decisions rest on, so what it counts and
what it refuses to count is the whole test. The cases that matter are the two
it exists to separate: a run that created directories and listed files
produced nothing, and a run that wrote code under a name nobody declared
produced something.
"""

import hashlib
import os
from pathlib import Path

import pytest
import structlog

from synthorg.engine.artifacts.workspace_fingerprint import fingerprint_tree
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)
from tests._shared import make_named_pipe

pytestmark = pytest.mark.unit


def _write(path: Path, text: str = "x") -> Path:
    """Write *text* to *path*, creating its parents.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _digest(path: Path) -> str:
    """Digest the bytes actually on disk.

    Read back rather than computed from the source text, so a platform that
    translates line endings does not turn a path assertion into an encoding
    assertion.

    Returns:
        The hex digest.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _link(link: Path, target: str) -> Path:
    """Create a symlink, skipping where the platform will not allow one.

    Windows refuses without the create-symbolic-link privilege, which no CI
    runner grants by default; the sandboxes this guards run Linux.

    Returns:
        The link created.
    """
    try:
        link.symlink_to(target)
    except OSError as exc:  # pragma: no cover - platform-dependent
        pytest.skip(f"symlinks unavailable here: {exc}")
    return link


class TestWhatCountsAsProduced:
    def test_an_absent_root_holds_nothing(self, tmp_path: Path) -> None:
        assert fingerprint_tree(tmp_path / "never-provisioned") == frozenset()

    def test_an_empty_directory_is_not_a_file(self, tmp_path: Path) -> None:
        """The recorded run's fifth turn was ``mkdir -p sqlcsv tests``.

        It was read as delivery by a proxy that counts tool calls. A
        directory holds nothing, so it must read as nothing here.
        """
        (tmp_path / "sqlcsv").mkdir()
        (tmp_path / "tests" / "fixtures").mkdir(parents=True)

        assert fingerprint_tree(tmp_path) == frozenset()

    def test_a_written_file_counts_with_its_path_and_content(
        self, tmp_path: Path
    ) -> None:
        written = _write(tmp_path / "sqlcsv" / "reader.py", "# real work\n")

        assert fingerprint_tree(tmp_path) == frozenset(
            {("sqlcsv/reader.py", _digest(written))}
        )

    def test_a_file_at_the_root_carries_no_leading_dot(self, tmp_path: Path) -> None:
        """A relative path of ``.`` must not prefix every top-level entry."""
        _write(tmp_path / "Makefile")

        assert {path for path, _ in fingerprint_tree(tmp_path)} == {"Makefile"}

    def test_an_edit_that_changes_length_changes_the_fingerprint(
        self, tmp_path: Path
    ) -> None:
        target = _write(tmp_path / "a.py", "# first\n")
        before = fingerprint_tree(tmp_path)
        target.write_text("# rewritten, and longer\n", encoding="utf-8")

        assert fingerprint_tree(tmp_path) != before

    def test_an_edit_that_keeps_the_length_changes_the_fingerprint(
        self, tmp_path: Path
    ) -> None:
        """A flipped constant is ordinary work, and a byte count cannot see it.

        Fingerprinted by size, this run reads as having produced nothing
        anywhere, which is what fails a task the reviewer should have seen.
        """
        target = _write(tmp_path / "a.py", "LIMIT = 10\n")
        before = fingerprint_tree(tmp_path)
        target.write_text("LIMIT = 99\n", encoding="utf-8")

        assert fingerprint_tree(tmp_path) != before

    def test_a_removal_changes_the_fingerprint(self, tmp_path: Path) -> None:
        target = _write(tmp_path / "a.py")
        before = fingerprint_tree(tmp_path)
        target.unlink()

        assert fingerprint_tree(tmp_path) != before


class TestWhatIsPruned:
    @pytest.mark.parametrize(
        "generated",
        [".git", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"],
    )
    def test_a_generated_tree_is_not_delivery(
        self, tmp_path: Path, generated: str
    ) -> None:
        """An agent that ran the suite it was handed authored nothing.

        Every one of these appears without a line being written, so counting
        them would wave through the run this check exists to catch.
        """
        _write(tmp_path / generated / "whatever")

        assert fingerprint_tree(tmp_path) == frozenset()

    def test_a_generated_tree_is_pruned_at_any_depth(self, tmp_path: Path) -> None:
        _write(tmp_path / "sqlcsv" / "__pycache__" / "reader.cpython-314.pyc")

        assert fingerprint_tree(tmp_path) == frozenset()

    def test_an_excluded_child_is_left_out(self, tmp_path: Path) -> None:
        _write(tmp_path / ".children" / "00-lexer" / "lexer.py")
        _write(tmp_path / "README.md")

        assert (
            fingerprint_tree(tmp_path, exclude={".children", "README.md"})
            == frozenset()
        )

    def test_exclusion_applies_to_the_root_only(self, tmp_path: Path) -> None:
        """A caller excluding the brief it mounted still counts one written.

        The exclusion names what the caller put there, which is a fact about
        the root's own children. Applied at every depth it would silently
        discard a file the run authored inside a package it created.
        """
        _write(tmp_path / "README.md")
        _write(tmp_path / "sqlcsv" / "README.md")

        assert {
            path for path, _ in fingerprint_tree(tmp_path, exclude={"README.md"})
        } == {"sqlcsv/README.md"}


class TestNothingIsOpenedThroughALink:
    """A workspace an agent can write is a workspace that can hold a trap.

    ``Path.is_file`` follows a link, so a link to ``/dev/zero`` hashed as a
    regular file never reaches EOF and the thread this runs on never returns.
    The delivery check awaits that thread, so the whole task hangs.
    """

    def test_a_symlink_is_keyed_by_its_text_not_its_target(
        self, tmp_path: Path
    ) -> None:
        target = _write(tmp_path / "real.py", "# authored\n")
        _link(tmp_path / "alias.py", "real.py")

        assert fingerprint_tree(tmp_path) == frozenset(
            {("real.py", _digest(target)), ("alias.py", "<symlink>real.py")}
        )

    def test_a_link_to_an_endless_device_returns(self, tmp_path: Path) -> None:
        """The reported hang, as a test. It passes by finishing at all."""
        if not Path("/dev/zero").exists():  # pragma: no cover - Windows
            pytest.skip("no character device to link at")
        _link(tmp_path / "trap", "/dev/zero")

        assert fingerprint_tree(tmp_path) == frozenset({("trap", "<symlink>/dev/zero")})

    def test_a_symlinked_directory_is_recorded_rather_than_walked(
        self, tmp_path: Path
    ) -> None:
        """The walk does not follow it, so nothing else would record it."""
        _write(tmp_path / "pkg" / "mod.py", "# authored\n")
        _link(tmp_path / "alias", "pkg")

        assert ("alias", "<symlink>pkg") in fingerprint_tree(tmp_path)

    def test_a_named_pipe_is_keyed_by_its_kind(self, tmp_path: Path) -> None:
        """Opening one blocks until somebody writes, which nobody will."""
        make_named_pipe(tmp_path / "pipe")

        assert [path for path, _ in fingerprint_tree(tmp_path)] == ["pipe"]


class TestAFilesystemThatRefusesToAnswer:
    """Missing evidence must never read as an absence.

    This fingerprint is the sole evidence behind failing a run for having
    produced nothing anywhere, so a path silently dropped from it is a
    delivered run failed on a permission error.
    """

    def test_an_unreadable_file_keeps_its_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marked rather than dropped, and never at its neighbours' cost."""
        readable = _write(tmp_path / "readable.py", "xyz")
        refused = _write(tmp_path / "refused.py", "xyz")
        real_lstat = Path.lstat

        def _refuse(self: Path) -> object:
            if self == refused:
                raise PermissionError(13, "refused", str(self))
            return real_lstat(self)

        monkeypatch.setattr(Path, "lstat", _refuse)

        assert fingerprint_tree(tmp_path) == frozenset(
            {("readable.py", _digest(readable)), ("refused.py", "<unreadable>")}
        )

    def test_a_subtree_the_walk_cannot_enter_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Path.walk`` discards its own error unless handed a handler.

        Without one the subtree simply vanishes and the verdict it drives is
        reached with nothing in the log to say the tree was never fully read.
        """
        readable = _write(tmp_path / "readable.py", "xy")
        refused = _write(tmp_path / "locked" / "hidden.py", "xyz").parent
        real_scandir = os.scandir

        def _refuse(path: str | Path = ".") -> object:
            if Path(path) == refused:
                raise PermissionError(13, "refused", str(path))
            return real_scandir(path)

        monkeypatch.setattr(os, "scandir", _refuse)

        with structlog.testing.capture_logs() as captured:
            fingerprint = fingerprint_tree(tmp_path)

        # The readable half survives: one refused subtree must not cost the
        # answer for everything beside it.
        assert fingerprint == frozenset({("readable.py", _digest(readable))})
        assert [
            entry["event"]
            for entry in captured
            if entry["event"] == EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED
        ] == [EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED]
