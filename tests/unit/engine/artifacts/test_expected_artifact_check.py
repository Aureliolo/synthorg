"""Unit tests for the expected-artifact existence check.

The check answers the question the zero-tool-call proxy only stands in for:
are the paths the task declared actually on disk. Its verdict decides
whether a run reaches review or is failed, so the boundary cases matter
most: an absent workspace, partial delivery, a path escaping the
workspace, a declaration that is prose rather than a path, and a
declaration naming somewhere the run could never have written.
"""

from pathlib import Path

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.expected_artifact_check import (
    is_probeable_path,
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


class TestIsProbeablePath:
    @pytest.mark.parametrize(
        "spec",
        [
            "src/game.py",
            "tests/test_game.py",
            "web/dist",
            "README.md",
            "a\\b.txt",
            "dist",
            "Makefile",
        ],
    )
    def test_path_shaped_declarations_are_probeable(self, spec: str) -> None:
        assert is_probeable_path(spec)

    @pytest.mark.parametrize(
        "spec",
        [
            "the integrated, runnable deliverable",
            "the end-to-end test run over the integrated deliverable",
            "a playable browser front end",
            "",
            "   ",
        ],
    )
    def test_prose_declarations_are_not_probeable(self, spec: str) -> None:
        """A deliverable name is not a filename.

        The planner writes free text, and the integration task's own
        declarations are sentences. Probing one finds no file, which would
        read as "produced nothing" and fail the task.
        """
        assert not is_probeable_path(spec)

    @pytest.mark.parametrize("spec", ["/etc/passwd", "C:\\Windows\\system.ini"])
    def test_absolute_declarations_are_not_probeable(self, spec: str) -> None:
        """Containment is what makes the answer about the task's own output."""
        assert not is_probeable_path(spec)


class TestMissingExpectedArtifacts:
    def test_all_present_reports_nothing_missing(self, tmp_path: Path) -> None:
        _touch(tmp_path, "src/game.py")
        _touch(tmp_path, "tests/test_game.py")

        presence = missing_expected_artifacts(
            _expected("src/game.py", "tests/test_game.py"), workspace=tmp_path
        )

        assert presence.missing == ()
        assert not presence.nothing_delivered

    def test_all_absent_reports_every_path(self, tmp_path: Path) -> None:
        presence = missing_expected_artifacts(
            _expected("src/game.py", "tests/test_game.py"), workspace=tmp_path
        )

        assert presence.missing == ("src/game.py", "tests/test_game.py")
        assert presence.nothing_delivered

    def test_partial_delivery_reports_only_the_absent(self, tmp_path: Path) -> None:
        """Partial delivery is a judgement call, so the caller sees which."""
        _touch(tmp_path, "src/game.py")

        presence = missing_expected_artifacts(
            _expected("src/game.py", "tests/test_game.py"), workspace=tmp_path
        )

        assert presence.missing == ("tests/test_game.py",)
        assert not presence.nothing_delivered

    def test_declaration_order_is_preserved(self, tmp_path: Path) -> None:
        presence = missing_expected_artifacts(
            _expected("z.py", "a.py"), workspace=tmp_path
        )

        assert presence.missing == ("z.py", "a.py")

    def test_a_directory_counts_as_delivered(self, tmp_path: Path) -> None:
        """A task may legitimately declare a directory deliverable."""
        (tmp_path / "web/dist").mkdir(parents=True)

        presence = missing_expected_artifacts(_expected("web/dist"), workspace=tmp_path)

        assert presence.missing == ()

    def test_absent_workspace_reports_every_path(self, tmp_path: Path) -> None:
        """An unprovisioned workspace means nothing was produced."""
        presence = missing_expected_artifacts(
            _expected("src/game.py"), workspace=tmp_path / "never-provisioned"
        )

        assert presence.missing == ("src/game.py",)
        assert presence.nothing_delivered

    def test_no_declared_artifacts_reports_nothing(self, tmp_path: Path) -> None:
        presence = missing_expected_artifacts((), workspace=tmp_path)

        assert presence.probed == ()
        assert not presence.nothing_delivered

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

        presence = missing_expected_artifacts(
            _expected(f"../{outside.name}"), workspace=workspace
        )

        assert presence.missing == (f"../{outside.name}",)

    def test_an_existing_absolute_path_cannot_stand_in_for_delivery(
        self, tmp_path: Path
    ) -> None:
        """An absolute declaration is never probed, so it proves nothing.

        Probing one would let a task that produced nothing read as delivered
        by naming any file that happens to exist on the host.
        """
        elsewhere = tmp_path / "elsewhere" / "artifact.bin"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_text("delivered", encoding="utf-8")
        workspace = tmp_path / "project"
        workspace.mkdir()

        presence = missing_expected_artifacts(
            _expected(str(elsewhere), "src/game.py"), workspace=workspace
        )

        assert presence.probed == ("src/game.py",)
        assert presence.nothing_delivered

    def test_prose_declarations_are_not_a_verdict(self, tmp_path: Path) -> None:
        """The integration task declares sentences, and must not fail for it.

        ``INTEGRATION_ARTIFACTS`` is prose. Probing it as a path would fail
        every integration task, so ``INTEGRATING -> EVALUATING`` would never
        fire and no initiative could ever complete.
        """
        presence = missing_expected_artifacts(
            _expected(
                "the integrated, runnable deliverable",
                "the end-to-end test run over the integrated deliverable",
            ),
            workspace=tmp_path,
        )

        assert presence.probed == ()
        assert not presence.nothing_delivered

    def test_a_delivered_file_beside_prose_is_not_a_failure(
        self, tmp_path: Path
    ) -> None:
        _touch(tmp_path, "src/game.py")

        presence = missing_expected_artifacts(
            _expected("a runnable deliverable", "src/game.py"), workspace=tmp_path
        )

        assert presence.probed == ("src/game.py",)
        assert not presence.nothing_delivered


class TestWorkspaceArtifactProbe:
    async def test_probe_resolves_the_projects_own_directory(
        self, tmp_path: Path
    ) -> None:
        _touch(tmp_path, "projects/proj-1/src/game.py")
        probe = workspace_artifact_probe(tmp_path)

        presence = await probe("proj-1", _expected("src/game.py"))

        assert presence.missing == ()

    async def test_another_projects_delivery_does_not_count(
        self, tmp_path: Path
    ) -> None:
        """Two projects share a root; one must not satisfy the other's task."""
        _touch(tmp_path, "projects/proj-1/src/game.py")
        probe = workspace_artifact_probe(tmp_path)

        presence = await probe("proj-2", _expected("src/game.py"))

        assert presence.missing == ("src/game.py",)
        assert presence.nothing_delivered

    async def test_traversal_in_the_project_id_is_refused(self, tmp_path: Path) -> None:
        probe = workspace_artifact_probe(tmp_path)

        with pytest.raises(WorkspaceSetupError, match="traversal"):
            await probe("../escape", _expected("src/game.py"))
