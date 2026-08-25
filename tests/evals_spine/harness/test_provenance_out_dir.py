# module-kind: tests
"""A recorder's own output must not make the tree it measured read as edited.

A recording harness writes its committed artifact into a TRACKED directory, and
``git_dirty`` is part of the resume identity, so without the exclusion a
finished stage dirties the tree with its own report and the next ``--resume`` is
refused on a mismatch, forfeiting every cell already paid for.

These drive the real ``git`` binary via subprocess, so they sit in the
integration capability rather than slowing the unit suite.
"""

import subprocess
from pathlib import Path

import pytest

from evals.harness.provenance import capture_git_state

pytestmark = pytest.mark.integration


def _git(*args: str, repo: Path) -> None:
    """Run one setup command in *repo*."""
    subprocess.run(  # noqa: S603 -- fixed argv, no shell, test-owned paths
        ["git", *args],  # noqa: S607 -- resolved on PATH, as every other test does
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository with one commit, so ``HEAD`` resolves.

    Returns:
        The repository root.
    """
    _git("init", "--initial-branch=main", repo=tmp_path)
    _git("config", "user.email", "harness@example.invalid", repo=tmp_path)
    _git("config", "user.name", "Harness", repo=tmp_path)
    _git("config", "commit.gpgsign", "false", repo=tmp_path)
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    _git("add", "-A", repo=tmp_path)
    _git("commit", "-m", "initial", repo=tmp_path)
    return tmp_path


def test_a_clean_tree_reads_clean(repo: Path) -> None:
    """The baseline the other two are read against."""
    assert capture_git_state(repo, ignoring=repo / "results").dirty is False


def test_the_recorders_own_output_does_not_dirty_the_tree(repo: Path) -> None:
    """The artifact a recording writes is not a change to the code it measured."""
    results = repo / "results"
    results.mkdir()
    (results / "chart.svg").write_text("<svg/>", encoding="utf-8")
    (results / "cells.jsonl").write_text("{}\n", encoding="utf-8")

    assert capture_git_state(repo, ignoring=results).dirty is False


def test_a_real_source_edit_still_reads_dirty(repo: Path) -> None:
    """The guard the exclusion must not weaken: a mid-sweep code change.

    A fix applied while a matrix is in flight changes the system under
    measurement, so the resume that would mix two systems into one curve has to
    keep being refused.
    """
    (repo / "source.py").write_text("value = 2\n", encoding="utf-8")

    assert capture_git_state(repo, ignoring=repo / "results").dirty is True


def test_an_out_dir_outside_the_repository_excludes_nothing(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A pathspec is relative to the repository, so an outside path has none.

    Nothing it holds could have appeared in the status anyway, so the query
    falls back to the plain one rather than building an unresolvable exclusion.
    """
    (repo / "source.py").write_text("value = 3\n", encoding="utf-8")
    elsewhere = tmp_path_factory.mktemp("outside")

    assert capture_git_state(repo, ignoring=elsewhere).dirty is True


def test_an_out_dir_at_the_repository_root_excludes_nothing(repo: Path) -> None:
    """``--out-dir .`` must not silence the dirty check for the whole worktree.

    The exclusion is a pathspec, and ``:(exclude).`` matches every tracked
    path, so a recording writing to the root would read clean however much
    source it had edited and a resume would mix two source states under one
    provenance record. The root is therefore the same case as an outside path,
    for the opposite reason.
    """
    (repo / "source.py").write_text("value = 4\n", encoding="utf-8")

    assert capture_git_state(repo, ignoring=repo).dirty is True


def test_no_out_dir_reads_the_whole_tree(repo: Path) -> None:
    """The pre-existing behaviour, for a caller that writes nothing tracked."""
    (repo / "results").mkdir()
    (repo / "results" / "chart.svg").write_text("<svg/>", encoding="utf-8")

    assert capture_git_state(repo).dirty is True
