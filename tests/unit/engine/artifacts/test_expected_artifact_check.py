"""Unit tests for the expected-artifact existence check.

The check answers the question the zero-tool-call proxy only stands in for:
are the paths the task declared actually on disk. Its verdict decides
whether a run reaches review or is failed, so the boundary cases matter
most: an absent workspace, partial delivery, a path escaping the
workspace, a declaration that is prose rather than a path, and a
declaration naming somewhere the run could never have written.
"""

from pathlib import Path
from typing import IO

import pytest

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.expected_artifact_check import (
    is_probeable_path,
    missing_expected_artifacts,
)

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


def _touch(root: Path, relpath: str, body: str = "delivered") -> Path:
    """Write *body* to *relpath* under *root*, creating its parents.

    Returns:
        The path written.
    """
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


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

    @pytest.mark.parametrize("declared", [".", "src/..", "./"])
    def test_declaring_the_workspace_itself_is_not_delivery(
        self, tmp_path: Path, declared: str
    ) -> None:
        """The workspace directory exists whether or not anything was written.

        Left probeable, a run could declare ``.``, produce nothing, and pass
        the check on the existence of the directory it was handed.
        """
        workspace = tmp_path / "project"
        workspace.mkdir()

        presence = missing_expected_artifacts(_expected(declared), workspace=workspace)

        assert presence.probed == (declared,)
        assert presence.nothing_delivered

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


class TestDeliveryAgainstABaseline:
    """Presence answers a create; only a baseline answers a change.

    Most engineering work edits a file that is already there, and for those
    tasks "the declared path exists" was true before the agent started. The
    baseline is what turns the probe back into a question about this run.
    """

    def test_an_untouched_file_delivered_nothing(self, tmp_path: Path) -> None:
        _touch(tmp_path, "ledger/accounts.py")
        expected = _expected("ledger/accounts.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert presence.missing == ()
        assert not presence.nothing_delivered
        assert presence.delivered_nothing_since(baseline)

    def test_an_edited_file_delivered(self, tmp_path: Path) -> None:
        _touch(tmp_path, "ledger/accounts.py")
        expected = _expected("ledger/accounts.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)
        (tmp_path / "ledger/accounts.py").write_text("fixed", encoding="utf-8")

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_nothing_since(baseline)

    def test_a_newly_created_file_delivered(self, tmp_path: Path) -> None:
        expected = _expected("textkit.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)
        _touch(tmp_path, "textkit.py")

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_nothing_since(baseline)

    def test_changing_one_of_several_is_delivery(self, tmp_path: Path) -> None:
        """The threshold matches the presence rule: none, not some."""
        _touch(tmp_path, "report/build.py")
        _touch(tmp_path, "report/parse.py")
        expected = _expected("report/build.py", "report/parse.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)
        (tmp_path / "report/parse.py").write_text("split out", encoding="utf-8")

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_nothing_since(baseline)

    def test_a_still_absent_file_is_not_read_as_unchanged(self, tmp_path: Path) -> None:
        """Absent-then-absent is the presence rule's failure, not a no-change."""
        expected = _expected("textkit.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert presence.nothing_delivered
        assert presence.delivered_nothing_since(baseline)

    def test_a_deleted_file_is_a_change(self, tmp_path: Path) -> None:
        """Removing a declared file is work, and the presence rule judges it."""
        _touch(tmp_path, "legacy/old.py")
        expected = _expected("legacy/old.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)
        (tmp_path / "legacy/old.py").unlink()

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_nothing_since(baseline)

    def test_a_directory_declaration_is_judged_on_presence_alone(
        self, tmp_path: Path
    ) -> None:
        """A tree has no single digest, so the baseline says nothing about it.

        Reading an unhashable declaration as unchanged would fail every run
        that declared a directory, so it keeps the presence answer.
        """
        (tmp_path / "dist").mkdir()
        expected = _expected("dist")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_nothing_since(baseline)

    def test_an_unhashable_declaration_is_not_evidence_of_delivery(
        self, tmp_path: Path
    ) -> None:
        """The two questions are not complements, and this is the gap.

        Reading "could not hash it" as "it changed" would let a run that
        touched nothing anywhere pass by having declared a directory the seed
        already provided. Fail-open belongs to the sibling, whose job is not
        to fail a run over evidence never gathered; this one ASSERTS, so the
        same absence has to answer no.
        """
        (tmp_path / "dist").mkdir()
        expected = _expected("dist")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_something_since(baseline)
        assert not presence.delivered_nothing_since(baseline)

    def test_a_declaration_that_became_a_directory_is_not_a_delivery(
        self, tmp_path: Path
    ) -> None:
        """Unhashable on ONE side is the same absence as on both.

        The two sides differ here, so comparing them naively reads as a
        change. What actually happened is that the module read a content
        before the run and none after, which asserts nothing about delivery.
        """
        target = _touch(tmp_path, "build", "a file, for now\n")
        expected = _expected("build")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)
        target.unlink()
        target.mkdir()

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_something_since(baseline)

    def test_a_same_length_edit_is_a_delivery(self, tmp_path: Path) -> None:
        """The case a size comparison cannot see.

        Flipping a constant keeps a file's byte count, so the whole-tree
        fingerprint reads it as untouched while the digest proves the run
        rewrote what it promised.
        """
        target = _touch(tmp_path, "src/config.py", "RETRIES = 1\n")
        expected = _expected("src/config.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)
        target.write_text("RETRIES = 5\n", encoding="utf-8")

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert presence.delivered_something_since(baseline)
        assert not presence.delivered_nothing_since(baseline)

    def test_no_baseline_falls_back_to_presence(self, tmp_path: Path) -> None:
        """An unwired baseline must not fail a run that delivered."""
        _touch(tmp_path, "ledger/accounts.py")
        expected = _expected("ledger/accounts.py")

        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.delivered_nothing_since(None)


class TestAnUnreadableDeclaration:
    def test_an_unreadable_file_does_not_lose_its_siblings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One refused path must not take the answer for every other with it.

        The digest is the only place this check opens a file, so an I/O error
        there would otherwise propagate out of the whole computation and the
        run would reach review with no declared-artifact verdict at all.
        """
        _touch(tmp_path, "src/reachable.py")
        _touch(tmp_path, "src/refused.py")
        real_open = Path.open

        def _refuse_one(self: Path, *_args: object, **_kwargs: object) -> IO[bytes]:
            if self.name == "refused.py":
                msg = "permission denied"
                raise PermissionError(msg)
            # The digest is the only caller and always opens binary.
            return real_open(self, "rb")

        monkeypatch.setattr(Path, "open", _refuse_one)

        presence = missing_expected_artifacts(
            _expected("src/reachable.py", "src/refused.py"), workspace=tmp_path
        )

        assert presence.missing == ()
        assert presence.digests["src/refused.py"] == "<unhashable>"

    def test_an_unreadable_file_is_not_read_as_undelivered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Evidence we could not read is not evidence the run wrote nothing."""
        _touch(tmp_path, "src/refused.py")
        expected = _expected("src/refused.py")
        baseline = missing_expected_artifacts(expected, workspace=tmp_path)

        def _refuse(self: Path, *_args: object, **_kwargs: object) -> IO[bytes]:
            del self
            msg = "permission denied"
            raise PermissionError(msg)

        monkeypatch.setattr(Path, "open", _refuse)
        presence = missing_expected_artifacts(expected, workspace=tmp_path)

        assert not presence.nothing_delivered
        assert not presence.delivered_nothing_since(baseline)
