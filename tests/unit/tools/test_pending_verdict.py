"""What a test run records as ``passed`` once a project declares pending tests.

The oracle is a pure function of this field, so this is where a skeleton's
by-design red suite becomes the green trunk the loop rests on, and where a unit
that left its marker behind stays red however cleanly it exited.
"""

from pathlib import Path

import pytest

from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME
from synthorg.tools._pending_verdict import resolve_passed

pytestmark = pytest.mark.unit

_PROJECT = "proj-1"
_TEST_ID = "tests/test_score.py::test_a_score_is_recorded"
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
        The base root the resolver is handed.
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


class TestWhenNothingIsDeclaredPending:
    """The exit status is the honest answer and is left alone."""

    @pytest.mark.parametrize("exited_zero", [True, False])
    def test_a_workspace_with_no_manifest_keeps_the_exit_status(
        self, tmp_path: Path, exited_zero: bool
    ) -> None:
        base = _workspace(tmp_path)

        assert (
            resolve_passed(
                exited_zero=exited_zero, workspace_root=base, project_id=_PROJECT
            )
            is exited_zero
        )

    @pytest.mark.parametrize("exited_zero", [True, False])
    def test_a_manifest_with_an_empty_pending_set_keeps_the_exit_status(
        self, tmp_path: Path, exited_zero: bool
    ) -> None:
        base = _workspace(tmp_path)
        _write(
            base, DEFAULT_MANIFEST_FILENAME, "language: python\ntest_command: pytest"
        )

        assert (
            resolve_passed(
                exited_zero=exited_zero, workspace_root=base, project_id=_PROJECT
            )
            is exited_zero
        )

    @pytest.mark.parametrize("exited_zero", [True, False])
    def test_an_unwired_workspace_root_keeps_the_exit_status(
        self, exited_zero: bool
    ) -> None:
        """Guessing at a workspace is worse than not correcting the status."""
        assert (
            resolve_passed(
                exited_zero=exited_zero, workspace_root=None, project_id=_PROJECT
            )
            is exited_zero
        )


class TestWhenPendingIsDeclared:
    def test_a_suite_failing_only_its_pending_tests_records_a_pass(
        self, tmp_path: Path
    ) -> None:
        """The whole point: a correct skeleton exits non-zero by design.

        Recording that as a failed build blocks the deliverable the contract
        stage exists to produce, and the oracle has no other field to read.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case(_ASSERTION_FAILURE)))

        assert (
            resolve_passed(exited_zero=False, workspace_root=base, project_id=_PROJECT)
            is True
        )

    def test_an_ordinary_test_breaking_alongside_them_records_a_failure(
        self, tmp_path: Path
    ) -> None:
        """The exit status cannot say this, because the pending failures spent it.

        A single non-zero bit is all a run has, and the declared failures have
        already claimed it, so reading the pending verdict alone would pass a
        run that also broke real tests.
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

        assert (
            resolve_passed(exited_zero=False, workspace_root=base, project_id=_PROJECT)
            is False
        )

    def test_a_pending_test_that_passes_records_a_failure(self, tmp_path: Path) -> None:
        """Clearing the marker is the signal, so leaving it behind is the defect.

        The suite exits zero, so nothing but this reading can catch a unit that
        satisfied its contract and left the marker for the next one to inherit.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case("")))

        assert (
            resolve_passed(exited_zero=True, workspace_root=base, project_id=_PROJECT)
            is False
        )

    def test_a_pending_test_that_errored_records_a_failure(
        self, tmp_path: Path
    ) -> None:
        """A skeleton that does not import is wrong, not merely unimplemented."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(
            base,
            _REPORT,
            _report(_pending_case('<error message="ImportError"/>')),
        )

        assert (
            resolve_passed(exited_zero=False, workspace_root=base, project_id=_PROJECT)
            is False
        )

    def test_a_missing_report_records_a_failure(self, tmp_path: Path) -> None:
        """Without the report nothing was measured, so nothing is evidence."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert (
            resolve_passed(exited_zero=True, workspace_root=base, project_id=_PROJECT)
            is False
        )


class TestABrokenManifest:
    def test_a_manifest_that_will_not_parse_records_a_failure(
        self, tmp_path: Path
    ) -> None:
        """Present-but-broken is a defect its author has to fix.

        Falling back to the exit status here would hand a green verdict to a
        suite whose declaration nobody can check, which is the forgery the
        pending mechanism has to resist: an agent whose pending test passes
        could corrupt the manifest and buy back the pass.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, "language: [unclosed")

        assert (
            resolve_passed(exited_zero=True, workspace_root=base, project_id=_PROJECT)
            is False
        )

    def test_a_manifest_that_does_not_validate_records_a_failure(
        self, tmp_path: Path
    ) -> None:
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, "language: python\nnot_a_field: 1")

        assert (
            resolve_passed(exited_zero=True, workspace_root=base, project_id=_PROJECT)
            is False
        )
