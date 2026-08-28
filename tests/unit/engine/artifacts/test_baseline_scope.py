"""Unit tests for the pre-run workspace baseline.

The baseline is what turns "these files exist" into "this run delivered".
Its degradation path therefore decides whether a run that edited a file is
read as having produced nothing, so what each ``None`` means, and which
failures reach the caller, are the cases that matter.

It carries two views because two different decisions read it: the declared
artifacts, which answer whether a promise was kept, and the whole tree, which
answers whether anything happened at all. One probe takes both, so the two
cannot describe different moments.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.baseline_scope import (
    RunBaseline,
    capture_run_baseline,
    current_run_baseline,
    produced_nothing_since,
    run_baseline_scope,
    workspace_run_probe,
)
from synthorg.engine.artifacts.expected_artifact_check import ArtifactPresence
from synthorg.engine.errors import WorkspaceSetupError

pytestmark = pytest.mark.unit

_EXPECTED = (ExpectedArtifact(type=ArtifactType.CODE, path=NotBlankStr("src/a.py")),)
_PRESENCE = ArtifactPresence(
    probed=("src/a.py",), missing=(), digests={"src/a.py": "abc123"}
)
_ANSWER = RunBaseline(workspace=Path("unused"), declared=_PRESENCE)


def _expected(*paths: str) -> tuple[ExpectedArtifact, ...]:
    """Declare *paths* as expected code artifacts.

    Returns:
        The declarations.
    """
    return tuple(
        ExpectedArtifact(type=ArtifactType.CODE, path=NotBlankStr(path))
        for path in paths
    )


def _touch(root: Path, relative: str) -> Path:
    """Create a file at *relative* under *root*.

    Returns:
        The path written.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


async def _answering(
    _project_id: str, _expected: Sequence[ExpectedArtifact]
) -> RunBaseline:
    """Return a fixed baseline, as a wired probe would.

    Returns:
        The canned answer.
    """
    return _ANSWER


class TestCaptureRunBaseline:
    async def test_a_wired_probe_supplies_the_baseline(self) -> None:
        captured = await capture_run_baseline(
            _answering, project_id="proj-1", expected=_EXPECTED
        )

        assert captured == _ANSWER

    async def test_nothing_declared_asks_nothing(self) -> None:
        """A task that declared no artifacts has no baseline to want."""

        async def _never_called(
            _project_id: str, _expected: Sequence[ExpectedArtifact]
        ) -> RunBaseline:
            msg = "probed a task that declared nothing"
            raise AssertionError(msg)

        assert (
            await capture_run_baseline(_never_called, project_id="proj-1", expected=())
            is None
        )

    async def test_an_unwired_probe_degrades(self) -> None:
        assert (
            await capture_run_baseline(None, project_id="proj-1", expected=_EXPECTED)
            is None
        )

    async def test_an_unreadable_workspace_degrades(self) -> None:
        """Storage faults must not fail a run that delivered."""

        async def _refusing(
            _project_id: str, _expected: Sequence[ExpectedArtifact]
        ) -> RunBaseline:
            msg = "workspace is not readable"
            raise PermissionError(msg)

        assert (
            await capture_run_baseline(
                _refusing, project_id="proj-1", expected=_EXPECTED
            )
            is None
        )

    async def test_a_probe_bug_reaches_the_caller(self) -> None:
        """The post-run half catches ``OSError`` alone, so this half must too.

        Swallowing a programming error here while the same error crashes the
        post-run probe would report one bug two incompatible ways: a silently
        disabled baseline on one side, a failed run on the other.
        """

        async def _broken(
            _project_id: str, _expected: Sequence[ExpectedArtifact]
        ) -> RunBaseline:
            msg = "probe called with the wrong shape"
            raise TypeError(msg)

        with pytest.raises(TypeError):
            await capture_run_baseline(_broken, project_id="proj-1", expected=_EXPECTED)


class TestWorkspaceRunProbe:
    async def test_probe_resolves_the_projects_own_directory(
        self, tmp_path: Path
    ) -> None:
        _touch(tmp_path, "projects/proj-1/src/game.py")
        probe = workspace_run_probe(tmp_path)

        baseline = await probe("proj-1", _expected("src/game.py"))

        assert baseline.declared.missing == ()

    async def test_another_projects_delivery_does_not_count(
        self, tmp_path: Path
    ) -> None:
        """Two projects share a root; one must not satisfy the other's task."""
        _touch(tmp_path, "projects/proj-1/src/game.py")
        probe = workspace_run_probe(tmp_path)

        baseline = await probe("proj-2", _expected("src/game.py"))

        assert baseline.declared.missing == ("src/game.py",)
        assert baseline.declared.nothing_delivered
        assert baseline.tree == frozenset()

    async def test_both_views_come_from_one_directory(self, tmp_path: Path) -> None:
        """The tree is the same workspace the declarations were read from."""
        _touch(tmp_path, "projects/proj-1/src/game.py")
        _touch(tmp_path, "projects/proj-1/notes.md")
        probe = workspace_run_probe(tmp_path)

        baseline = await probe("proj-1", _expected("src/game.py"))

        assert {path for path, _ in baseline.tree} == {"src/game.py", "notes.md"}
        assert baseline.workspace == tmp_path / "projects" / "proj-1"

    async def test_traversal_in_the_project_id_is_refused(self, tmp_path: Path) -> None:
        probe = workspace_run_probe(tmp_path)

        with pytest.raises(WorkspaceSetupError, match="traversal"):
            await probe("../escape", _expected("src/game.py"))


class TestRunBaselineScope:
    def test_the_scope_publishes_and_restores(self) -> None:
        assert current_run_baseline() is None
        with run_baseline_scope(_ANSWER):
            assert current_run_baseline() == _ANSWER
        assert current_run_baseline() is None

    def test_a_nested_scope_restores_the_outer_baseline(self) -> None:
        """Recovery retries nest inside the original run's scope."""
        outer = RunBaseline(
            workspace=Path("outer"),
            declared=ArtifactPresence(probed=("src/a.py",), missing=("src/a.py",)),
        )
        with run_baseline_scope(outer):
            with run_baseline_scope(_ANSWER):
                assert current_run_baseline() == _ANSWER
            assert current_run_baseline() == outer


class TestProducedNothingSince:
    """The one answer to "did this run do anything", asked three times."""

    async def test_no_baseline_is_not_a_verdict(self) -> None:
        """Missing evidence must never read as a run that delivered nothing."""
        assert await produced_nothing_since(None) is None

    async def test_an_untouched_workspace_produced_nothing(
        self, tmp_path: Path
    ) -> None:
        probe = workspace_run_probe(tmp_path)
        baseline = await probe("proj-1", _expected("src/game.py"))

        assert await produced_nothing_since(baseline) is True

    async def test_creating_directories_is_not_producing(self, tmp_path: Path) -> None:
        """The recorded run's whole output was ``mkdir -p sqlcsv tests``."""
        probe = workspace_run_probe(tmp_path)
        baseline = await probe("proj-1", _expected("sqlcsv/reader.py"))
        (baseline.workspace / "sqlcsv").mkdir(parents=True)
        (baseline.workspace / "tests").mkdir()

        assert await produced_nothing_since(baseline) is True

    async def test_an_undeclared_file_still_counts_as_produced(
        self, tmp_path: Path
    ) -> None:
        """The declaration is a guess made before the tree existed.

        Measured on a live cell: three of eight units wrote 4, 8 and 10
        modules apiece under names the planner had not guessed, and every one
        of them read as having produced nothing.
        """
        probe = workspace_run_probe(tmp_path)
        baseline = await probe("proj-1", _expected("sqlcsv/csv_reader.py"))
        _touch(baseline.workspace, "sqlcsv/reader.py")

        assert await produced_nothing_since(baseline) is False

    async def test_an_edit_to_a_file_that_was_there_counts(
        self, tmp_path: Path
    ) -> None:
        """Most engineering work edits; presence alone cannot see it."""
        _touch(tmp_path, "projects/proj-1/src/game.py")
        probe = workspace_run_probe(tmp_path)
        baseline = await probe("proj-1", _expected("src/game.py"))
        (baseline.workspace / "src" / "game.py").write_text(
            "much longer content\n", encoding="utf-8"
        )

        assert await produced_nothing_since(baseline) is False
