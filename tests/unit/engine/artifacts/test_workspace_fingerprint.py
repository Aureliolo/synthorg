"""Unit tests for the workspace fingerprint.

This is the evidence three delivery decisions rest on, so what it counts and
what it refuses to count is the whole test. The cases that matter are the two
it exists to separate: a run that created directories and listed files
produced nothing, and a run that wrote code under a name nobody declared
produced something.
"""

import os
from pathlib import Path

import pytest
import structlog

from synthorg.engine.artifacts.workspace_fingerprint import fingerprint_tree
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

pytestmark = pytest.mark.unit


def _write(path: Path, text: str = "x") -> Path:
    """Write *text* to *path*, creating its parents.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


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

    def test_a_written_file_counts_with_its_path_and_size(self, tmp_path: Path) -> None:
        written = _write(tmp_path / "sqlcsv" / "reader.py", "# real work\n")

        # Size read back rather than asserted: the platform may translate
        # line endings, and this pins the path, not the encoding.
        assert fingerprint_tree(tmp_path) == frozenset(
            {("sqlcsv/reader.py", written.stat().st_size)}
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


class TestAFilesystemThatRefusesToAnswer:
    """Missing evidence must never read as an absence.

    This fingerprint is the sole evidence behind failing a run for having
    produced nothing anywhere, so a path silently dropped from it is a
    delivered run failed on a permission error.
    """

    def test_an_unmeasurable_file_keeps_its_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sized ``-1`` rather than dropped, and never at its neighbours' cost."""
        _write(tmp_path / "readable.py", "xyz")
        refused = _write(tmp_path / "refused.py", "xyz")
        real_stat = Path.stat

        def _refuse(self: Path, **kwargs: object) -> object:
            if self == refused:
                raise PermissionError(13, "refused", str(self))
            return real_stat(self, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "stat", _refuse)

        assert fingerprint_tree(tmp_path) == frozenset(
            {("readable.py", 3), ("refused.py", -1)}
        )

    def test_a_subtree_the_walk_cannot_enter_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``Path.walk`` discards its own error unless handed a handler.

        Without one the subtree simply vanishes and the verdict it drives is
        reached with nothing in the log to say the tree was never fully read.
        """
        _write(tmp_path / "readable.py", "xy")
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
        assert fingerprint == frozenset({("readable.py", 2)})
        assert [
            entry["event"]
            for entry in captured
            if entry["event"] == EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED
        ] == [EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED]
