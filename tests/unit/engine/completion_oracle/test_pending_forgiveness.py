"""What a project's pending declaration says about one task's test run.

Two questions the exit status cannot answer, pointing opposite ways: whether a
failing run is the failure the project declared in advance, and whether a
passing run left its own criterion still marked unimplemented.
"""

from pathlib import Path

import pytest

from synthorg.engine.completion_oracle.pending_forgiveness import (
    failure_was_declared,
    unclaimed_criteria,
)
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME

pytestmark = pytest.mark.unit

_PROJECT = "proj-1"
_TEST_ID = "tests/test_score.py::test_a_score_is_recorded"
_CRITERION = "A score is recorded."
_REPORT = "reports/junit.xml"

_MANIFEST = f"""\
language: python
test_command: pytest
test_report_path: {_REPORT}
pending:
  - criterion: a score is recorded
    test_id: {_TEST_ID}
"""


def _workspace(tmp_path: Path) -> Path:
    """Build the project's workspace directory under a base root.

    Returns:
        The base root the readers are handed.
    """
    (tmp_path / "projects" / _PROJECT).mkdir(parents=True)
    return tmp_path


def _write(base: Path, name: str, body: str) -> None:
    """Write *body* to *name* inside the project's workspace."""
    path = base / "projects" / _PROJECT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _report(*cases: str) -> str:
    """Build a JUnit document out of pre-rendered ``testcase`` elements.

    Returns:
        The report body.
    """
    return f'<testsuite name="pytest">{"".join(cases)}</testsuite>'


def _pending_case(outcome: str) -> str:
    """Render the declared pending test's case, ending with *outcome*.

    Returns:
        One ``testcase`` element.
    """
    return (
        '<testcase classname="tests/test_score.py" '
        f'name="test_a_score_is_recorded">{outcome}</testcase>'
    )


def _other_case(outcome: str) -> str:
    """Render an ordinary test's case, ending with *outcome*.

    Returns:
        One ``testcase`` element.
    """
    return (
        '<testcase classname="tests/test_other.py" '
        f'name="test_something_else">{outcome}</testcase>'
    )


_ASSERTION_FAILURE = '<failure message="assert 0 == 1"/>'


class TestWhetherAFailureWasDeclared:
    def test_a_suite_failing_only_its_pending_tests_is_forgiven(
        self, tmp_path: Path
    ) -> None:
        """The whole point: a correct skeleton exits non-zero by design.

        Read as evidence, that run blocks the very deliverable the contract
        stage exists to produce.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case(_ASSERTION_FAILURE)))

        assert failure_was_declared(workspace_root=base, project_id=_PROJECT) is True

    def test_an_ordinary_test_breaking_alongside_them_is_not_forgiven(
        self, tmp_path: Path
    ) -> None:
        """The exit status cannot say this: the pending failures spent it.

        One non-zero bit is all a run has, and the declared failures have
        already claimed it, so reading the pending verdict alone would forgive
        a run that also broke real tests.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(
            base,
            _REPORT,
            _report(
                _pending_case(_ASSERTION_FAILURE),
                _other_case('<failure message="assert 2 == 3"/>'),
            ),
        )

        assert failure_was_declared(workspace_root=base, project_id=_PROJECT) is False

    def test_a_pending_test_that_errored_is_not_forgiven(self, tmp_path: Path) -> None:
        """A skeleton that does not import is wrong, not merely unimplemented."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case('<error message="ImportError"/>')))

        assert failure_was_declared(workspace_root=base, project_id=_PROJECT) is False

    def test_a_missing_report_forgives_nothing(self, tmp_path: Path) -> None:
        """Without the report nothing was measured, so nothing is evidence."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert failure_was_declared(workspace_root=base, project_id=_PROJECT) is False

    @pytest.mark.parametrize(
        ("manifest", "reason"),
        [
            pytest.param(None, "no manifest committed", id="no_manifest"),
            pytest.param(
                "language: python\ntest_command: pytest",
                "nothing declared pending",
                id="empty_pending_set",
            ),
            pytest.param("language: [unclosed", "broken manifest", id="unparseable"),
            pytest.param(
                "language: python\nnot_a_field: 1", "broken manifest", id="invalid"
            ),
        ],
    )
    def test_nothing_to_forgive_leaves_the_failure_standing(
        self, tmp_path: Path, manifest: str | None, reason: str
    ) -> None:
        """A present-but-broken manifest is a defect its author has to fix.

        Reading it as "nothing was pending" would hand the verdict back to an
        exit status whose declaration nobody can check, which is the forgery
        the mechanism has to resist: an agent whose pending test started
        passing could corrupt the manifest and buy the pass back.
        """
        base = _workspace(tmp_path)
        if manifest is not None:
            _write(base, DEFAULT_MANIFEST_FILENAME, manifest)

        assert failure_was_declared(workspace_root=base, project_id=_PROJECT) is False

    def test_an_unwired_workspace_forgives_nothing(self) -> None:
        """Guessing at a workspace is worse than leaving the failure standing."""
        assert failure_was_declared(workspace_root=None, project_id=_PROJECT) is False


class TestWhichCriteriaAreStillUnclaimed:
    def test_a_criterion_still_listed_is_reported(self, tmp_path: Path) -> None:
        """Clearing the entry in the same commit is the signal a unit is done.

        The suite exits zero either way, so nothing but this reading can catch
        a unit that implemented its criterion and left the marker for the next
        one to inherit.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert unclaimed_criteria(
            [_CRITERION], workspace_root=base, project_id=_PROJECT
        ) == (_CRITERION,)

    @pytest.mark.parametrize(
        "spelling",
        [
            pytest.param("a score is recorded", id="exact"),
            pytest.param("  A Score Is Recorded  ", id="case_and_whitespace"),
            pytest.param("A score is recorded.", id="trailing_full_stop"),
            pytest.param("a score is recorded!", id="trailing_exclamation"),
        ],
    )
    def test_the_match_survives_a_respelling(
        self, tmp_path: Path, spelling: str
    ) -> None:
        """The task's wording and the manifest's have different authors.

        The criterion travels from the objective into a brief into a manifest
        entry an agent typed, so a comparison that misses on a full stop the
        agent dropped reports every unit as done, silently, for ever.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert unclaimed_criteria(
            [spelling], workspace_root=base, project_id=_PROJECT
        ) == (spelling,)

    def test_another_units_criterion_is_not_this_units_problem(
        self, tmp_path: Path
    ) -> None:
        """Judged per criterion, never per project.

        A project mid-build always has other units' entries outstanding, and
        reading those would fail every unit until the last one.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert (
            unclaimed_criteria(
                ["something else entirely"],
                workspace_root=base,
                project_id=_PROJECT,
            )
            == ()
        )

    @pytest.mark.parametrize(
        ("manifest", "case_id"),
        [
            pytest.param(None, "no_manifest", id="no_manifest"),
            pytest.param("language: [unclosed", "broken", id="unparseable"),
        ],
    )
    def test_nothing_readable_claims_nothing(
        self, tmp_path: Path, manifest: str | None, case_id: str
    ) -> None:
        """A broken manifest must not fail every task in the project.

        It already forgives nothing on the failing side; blocking green runs
        as well would make one unparseable file a project-wide outage, which
        is a worse failure than the one it guards against.
        """
        base = _workspace(tmp_path)
        if manifest is not None:
            _write(base, DEFAULT_MANIFEST_FILENAME, manifest)

        assert (
            unclaimed_criteria([_CRITERION], workspace_root=base, project_id=_PROJECT)
            == ()
        )
