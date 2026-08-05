"""Unit tests for the expected-artifact existence check.

The check answers the question the zero-tool-call proxy only stands in for:
are the paths the task declared actually on disk. Its verdict decides
whether a run reaches review or is failed, so the boundary cases -- absent
workspace, partial delivery, a path escaping the workspace -- are the tests
that matter.
"""

from pathlib import Path

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.expected_artifact_check import (
    missing_expected_artifacts,
    workspace_artifact_probe,
)
from synthorg.engine.errors import WorkspaceSetupError

pytestmark = pytest.mark.unit


def _expected(*paths: str) -> tuple[ExpectedArtifact, ...]:
    """Declare *paths* as expected code artifacts.

    Returns:
        One :class:`ExpectedArtifact` per path.
    """
    return tuple(
        ExpectedArtifact(type=ArtifactType.CODE, path=NotBlankStr(path))
        for path in paths
    )


def _touch(root: Path, relpath: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("delivered", encoding="utf-8")


class TestMissingExpectedArtifacts:
    def test_all_present_reports_nothing_missing(self, tmp_path: Path) -> None:
        _touch(tmp_path, "src/game.py")
        _touch(tmp_path, "tests/test_game.py")

        missing = missing_expected_artifacts(
            _expected("src/game.py", "tests/test_game.py"), workspace=tmp_path
        )

        assert missing == ()

    def test_all_absent_reports_every_path(self, tmp_path: Path) -> None:
        missing = missing_expected_artifacts(
            _expected("src/game.py", "tests/test_game.py"), workspace=tmp_path
        )

        assert missing == ("src/game.py", "tests/test_game.py")

    def test_partial_delivery_reports_only_the_absent(self, tmp_path: Path) -> None:
        """Partial delivery is a judgement call, so the caller sees which."""
        _touch(tmp_path, "src/game.py")

        missing = missing_expected_artifacts(
            _expected("src/game.py", "tests/test_game.py"), workspace=tmp_path
        )

        assert missing == ("tests/test_game.py",)

    def test_declaration_order_is_preserved(self, tmp_path: Path) -> None:
        missing = missing_expected_artifacts(
            _expected("z.py", "a.py"), workspace=tmp_path
        )

        assert missing == ("z.py", "a.py")

    def test_a_directory_counts_as_delivered(self, tmp_path: Path) -> None:
        """A task may legitimately declare a directory deliverable."""
        (tmp_path / "web/dist").mkdir(parents=True)

        assert missing_expected_artifacts(
            _expected("web/dist"), workspace=tmp_path
        ) == (())

    def test_absent_workspace_reports_every_path(self, tmp_path: Path) -> None:
        """An unprovisioned workspace means nothing was produced."""
        missing = missing_expected_artifacts(
            _expected("src/game.py"), workspace=tmp_path / "never-provisioned"
        )

        assert missing == ("src/game.py",)

    def test_no_declared_artifacts_reports_nothing(self, tmp_path: Path) -> None:
        assert missing_expected_artifacts((), workspace=tmp_path) == ()

    def test_path_escaping_the_workspace_counts_as_absent(self, tmp_path: Path) -> None:
        """A file the run could not legitimately have written is not evidence.

        A traversal path may well resolve to something that exists; probing
        it would let a task claim delivery by naming a file outside its own
        workspace.
        """
        outside = tmp_path.parent / "outside.py"
        outside.write_text("not ours", encoding="utf-8")
        workspace = tmp_path / "project"
        workspace.mkdir()

        missing = missing_expected_artifacts(
            _expected(f"../{outside.name}"), workspace=workspace
        )

        assert missing == (f"../{outside.name}",)

    def test_absolute_path_is_probed_as_given(self, tmp_path: Path) -> None:
        """A planner may declare a path outside the workspace deliberately."""
        elsewhere = tmp_path / "elsewhere" / "artifact.bin"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("delivered", encoding="utf-8")
        workspace = tmp_path / "project"
        workspace.mkdir()

        assert (
            missing_expected_artifacts(_expected(str(elsewhere)), workspace=workspace)
            == ()
        )

    def test_absent_absolute_path_is_reported(self, tmp_path: Path) -> None:
        workspace = tmp_path / "project"
        workspace.mkdir()
        absent = str(tmp_path / "elsewhere" / "artifact.bin")

        assert missing_expected_artifacts(_expected(absent), workspace=workspace) == (
            absent,
        )


class TestWorkspaceArtifactProbe:
    def test_probe_resolves_the_projects_own_directory(self, tmp_path: Path) -> None:
        _touch(tmp_path, "projects/proj-1/src/game.py")
        probe = workspace_artifact_probe(tmp_path)

        assert probe("proj-1", _expected("src/game.py")) == ()

    def test_another_projects_delivery_does_not_count(self, tmp_path: Path) -> None:
        """Two projects share a root; one must not satisfy the other's task."""
        _touch(tmp_path, "projects/proj-1/src/game.py")
        probe = workspace_artifact_probe(tmp_path)

        assert probe("proj-2", _expected("src/game.py")) == ("src/game.py",)

    def test_traversal_in_the_project_id_is_refused(self, tmp_path: Path) -> None:
        probe = workspace_artifact_probe(tmp_path)

        with pytest.raises(WorkspaceSetupError, match="traversal"):
            probe("../escape", _expected("src/game.py"))
