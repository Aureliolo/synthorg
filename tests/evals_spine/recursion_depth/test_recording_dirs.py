# module-kind: tests
"""Which directories a dirty check must ignore when cells run concurrently.

Excluding only the caller's own out-dir left three concurrently-recorded cells
disagreeing about a tree none of them had changed: the first read clean, the
other two read dirty on the same commit, purely because the first cell's
directory had appeared. ``git_dirty`` is in the resume identity, so the clean
cell could then never be resumed.

A sibling qualifies only by PROOF that it is a recording, namely that it holds
one of this harness's journals. Excluding the parent wholesale is the obvious
shortcut and is unsafe, because ``--out-dir`` takes any path.
"""

from pathlib import Path

import pytest

from evals.recursion_depth.journal import JOURNAL_NAME, PROGRESS_NAME
from evals.recursion_depth.provenance import recording_dirs

pytestmark = pytest.mark.unit


def _recording(directory: Path, *, journal: str = JOURNAL_NAME) -> Path:
    """Make *directory* look like a finished or in-flight recording.

    Returns:
        The directory.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / journal).write_text("{}\n", encoding="utf-8")
    return directory


class TestRecordingDirs:
    """Its own directory always, a sibling only on proof."""

    def test_its_own_directory_is_always_first(self, tmp_path: Path) -> None:
        mine = tmp_path / "results" / "smoke-a"
        mine.mkdir(parents=True)

        assert recording_dirs(mine)[0] == mine

    def test_a_sibling_recording_is_excluded_too(self, tmp_path: Path) -> None:
        """The case that made a paid-for cell unresumable."""
        mine = _recording(tmp_path / "results" / "smoke-b")
        sibling = _recording(tmp_path / "results" / "smoke-a")

        assert set(recording_dirs(mine)) == {mine, sibling}

    def test_an_in_flight_sibling_counts(self, tmp_path: Path) -> None:
        """A running cell has written progress but not yet its cell journal."""
        mine = _recording(tmp_path / "results" / "smoke-b")
        sibling = _recording(tmp_path / "results" / "smoke-c", journal=PROGRESS_NAME)

        assert set(recording_dirs(mine)) == {mine, sibling}

    def test_a_sibling_that_is_not_a_recording_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        """Otherwise ``--out-dir src/x`` would exclude the whole of ``src``."""
        mine = _recording(tmp_path / "results" / "smoke-b")
        (tmp_path / "results" / "source").mkdir(parents=True)
        (tmp_path / "results" / "source" / "engine.py").write_text(
            "value = 1\n", encoding="utf-8"
        )

        assert recording_dirs(mine) == (mine,)

    def test_a_loose_file_beside_it_is_not_a_sibling(self, tmp_path: Path) -> None:
        mine = _recording(tmp_path / "results" / "smoke-b")
        (tmp_path / "results" / "notes.md").write_text("hi\n", encoding="utf-8")

        assert recording_dirs(mine) == (mine,)

    def test_a_parent_that_does_not_exist_yet_is_not_walked(
        self, tmp_path: Path
    ) -> None:
        """The first cell of a run stamps provenance before writing anything."""
        mine = tmp_path / "nothing" / "here"

        assert recording_dirs(mine) == (mine,)
