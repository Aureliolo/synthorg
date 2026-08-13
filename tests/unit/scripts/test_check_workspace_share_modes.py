"""Tests for the workspace share-mode gate."""

import ast

import pytest
from scripts.check_workspace_share_modes import (
    _marked,
    _mkstemp_without_mode,
    _raw_mkdir,
)

pytestmark = pytest.mark.unit


def _parsed(source: str) -> tuple[ast.Module, list[str]]:
    """Return the parsed tree and split lines for *source*."""
    return ast.parse(source), source.splitlines()


class TestAtomicWriteMode:
    def test_a_write_that_never_states_its_mode_is_flagged(self) -> None:
        tree, lines = _parsed(
            "def write(target, data):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    Path(tmp).replace(target)\n"
        )
        assert len(_mkstemp_without_mode(tree, lines)) == 1

    def test_an_fchmod_in_the_same_function_satisfies_it(self) -> None:
        tree, lines = _parsed(
            "def write(target, data):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    os.fchmod(fd, delivered_file_mode(None))\n"
            "    Path(tmp).replace(target)\n"
        )
        assert _mkstemp_without_mode(tree, lines) == []

    def test_a_path_chmod_also_satisfies_it(self) -> None:
        """Windows has no ``fchmod``; the fallback path counts too."""
        tree, lines = _parsed(
            "def write(target, data):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    Path(tmp).chmod(0o660)\n"
            "    Path(tmp).replace(target)\n"
        )
        assert _mkstemp_without_mode(tree, lines) == []

    def test_a_chmod_on_an_unrelated_path_does_not_satisfy_it(self) -> None:
        """The mode setter has to be aimed at the file being delivered.

        A function that sets the mode of something else entirely leaves the
        temp file at ``mkstemp``'s owner-only bits, which is the state this
        gate exists to catch, so accepting any mode setter anywhere in the
        function accepts precisely the defect.
        """
        tree, lines = _parsed(
            "def write(target, data):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    target.parent.chmod(0o2770)\n"
            "    Path(tmp).replace(target)\n"
        )
        assert len(_mkstemp_without_mode(tree, lines)) == 1

    def test_a_chmod_through_a_derived_name_satisfies_it(self) -> None:
        """A writer names the temp path once and works through that name."""
        tree, lines = _parsed(
            "def write(target, data):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    tmp_path = Path(tmp)\n"
            "    tmp_path.chmod(0o660)\n"
            "    tmp_path.replace(target)\n"
        )
        assert _mkstemp_without_mode(tree, lines) == []

    def test_an_fchmod_on_the_opened_descriptor_satisfies_it(self) -> None:
        """The shipped writers chmod the still-open handle, not the path."""
        tree, lines = _parsed(
            "def write(target, data):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    with os.fdopen(fd, 'wb') as handle:\n"
            "        os.fchmod(handle.fileno(), delivered_file_mode(None))\n"
            "    Path(tmp).replace(target)\n"
        )
        assert _mkstemp_without_mode(tree, lines) == []

    def test_a_function_with_no_temp_file_is_not_flagged(self) -> None:
        tree, lines = _parsed("def write(target, data):\n    target.write_text(data)\n")
        assert _mkstemp_without_mode(tree, lines) == []

    def test_an_async_writer_is_checked_too(self) -> None:
        tree, lines = _parsed(
            "async def write(target, data):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    Path(tmp).replace(target)\n"
        )
        assert len(_mkstemp_without_mode(tree, lines)) == 1


class TestWorkspaceMkdir:
    def test_a_direct_mkdir_is_flagged(self) -> None:
        tree, lines = _parsed("def setup(root):\n    root.mkdir(parents=True)\n")
        assert len(_raw_mkdir(tree, lines)) == 1

    def test_the_shared_helper_is_not_flagged(self) -> None:
        tree, lines = _parsed("def setup(root):\n    ensure_shared_dir(root)\n")
        assert _raw_mkdir(tree, lines) == []


class TestMarker:
    def test_a_marker_above_a_def_covers_it(self) -> None:
        """A function-level exemption is written where a reader meets it."""
        source = (
            "# lint-allow: workspace-share-mode -- backend-private\n"
            "def write(target):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
            "    Path(tmp).replace(target)\n"
        )
        tree, lines = _parsed(source)
        assert _mkstemp_without_mode(tree, lines) == []

    def test_a_marker_inside_the_span_covers_it(self) -> None:
        source = (
            "def write(target):\n"
            "    # lint-allow: workspace-share-mode -- backend-private\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
        )
        tree, lines = _parsed(source)
        assert _mkstemp_without_mode(tree, lines) == []

    def test_an_unrelated_comment_above_does_not_cover_it(self) -> None:
        source = (
            "# writes the thing\ndef write(target):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
        )
        tree, lines = _parsed(source)
        assert len(_mkstemp_without_mode(tree, lines)) == 1

    def test_a_marker_separated_by_a_blank_line_does_not_cover_it(self) -> None:
        """Only the contiguous block counts, so a stale marker cannot drift down."""
        source = (
            "# lint-allow: workspace-share-mode -- stale\n"
            "\n"
            "def write(target):\n"
            "    fd, tmp = tempfile.mkstemp(dir=target.parent)\n"
        )
        tree, lines = _parsed(source)
        node = tree.body[0]
        assert not _marked(lines, node)
