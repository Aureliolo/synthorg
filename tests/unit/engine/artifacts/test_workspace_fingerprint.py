"""Unit tests for the workspace fingerprint.

This is the evidence three delivery decisions rest on, so what it counts and
what it refuses to count is the whole test. The cases that matter are the two
it exists to separate: a run that created directories and listed files
produced nothing, and a run that wrote code under a name nobody declared
produced something.
"""

from pathlib import Path

import pytest

from synthorg.engine.artifacts.workspace_fingerprint import fingerprint_tree

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
