"""What a project's pending declaration says about one task's test run.

Two questions the exit status cannot answer, pointing opposite ways: whether a
failing run is the failure the project declared in advance, and whether a
passing run left its own criterion still marked unimplemented.

Plus the two narrowings that stop the first question being an escape hatch. The
declaration only counts for criteria the plan was approved with, and only
against a report at least as new as the run being judged.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from synthorg.engine.completion_oracle.pending_forgiveness import (
    ContractState,
    ContractView,
    approved_vocabulary,
    declared_gates,
    failure_was_declared,
    load_contract,
    unclaimed_criteria,
)
from synthorg.engine.workspace.environment.config import DEFAULT_MANIFEST_FILENAME

pytestmark = pytest.mark.unit

_PROJECT = "proj-1"
_TEST_ID = "tests/test_score.py::test_a_score_is_recorded"
_CRITERION = "A score is recorded."
_REPORT = "reports/junit.xml"
_APPROVED = approved_vocabulary([_CRITERION])

#: Every report these tests write is stamped now, and the run being judged is
#: dated before it, so freshness never accidentally decides a case that is
#: about something else.
_RAN_AT = datetime(2026, 1, 1, tzinfo=UTC)

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


async def _contract(base: Path | None) -> ContractView:
    """Read the project's contract the way the oracle does.

    Returns:
        The contract view every question is answered from.
    """
    return await load_contract(workspace_root=base, project_id=_PROJECT)


def _report(*cases: str) -> str:
    """Build a JUnit document out of pre-rendered ``testcase`` elements.

    Returns:
        The report body.
    """
    return f'<testsuite name="pytest">{"".join(cases)}</testsuite>'


def _case(module: str, name: str, outcome: str) -> str:
    """Render one ``testcase`` the way pytest writes it.

    ``classname`` is the dotted module path and the file lives in its own
    attribute, which is what the node id is rebuilt from.

    Returns:
        One ``testcase`` element.
    """
    return (
        f'<testcase classname="tests.{module}" file="tests/{module}.py" '
        f'name="{name}">{outcome}</testcase>'
    )


def _pending_case(outcome: str) -> str:
    """Render the declared pending test's case, ending with *outcome*.

    Returns:
        One ``testcase`` element.
    """
    return _case("test_score", "test_a_score_is_recorded", outcome)


def _other_case(outcome: str) -> str:
    """Render an ordinary test's case, ending with *outcome*.

    Returns:
        One ``testcase`` element.
    """
    return _case("test_other", "test_something_else", outcome)


_ASSERTION_FAILURE = '<failure message="assert 0 == 1"/>'


class TestWhetherAFailureWasDeclared:
    async def test_a_suite_failing_only_its_pending_tests_is_forgiven(
        self, tmp_path: Path
    ) -> None:
        """The whole point: a correct skeleton exits non-zero by design.

        Read as evidence, that run blocks the very deliverable the contract
        stage exists to produce.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case(_ASSERTION_FAILURE)))

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is True

    async def test_an_ordinary_test_breaking_alongside_them_is_not_forgiven(
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

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is False

    async def test_a_pending_test_that_errored_is_not_forgiven(
        self, tmp_path: Path
    ) -> None:
        """A skeleton that does not import is wrong, not merely unimplemented."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case('<error message="ImportError"/>')))

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is False

    async def test_a_missing_report_forgives_nothing(self, tmp_path: Path) -> None:
        """Without the report nothing was measured, so nothing is evidence."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is False

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
    async def test_nothing_to_forgive_leaves_the_failure_standing(
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

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is False

    async def test_an_unwired_workspace_forgives_nothing(self) -> None:
        """Guessing at a workspace is worse than leaving the failure standing."""
        declared = failure_was_declared(
            await _contract(None), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is False


class TestForgivenessIsBoundToTheApprovedCriteria:
    """An entry naming a criterion nobody approved declares nothing.

    The two questions key on different fields, which is what made this
    exploitable: forgiveness reads the test id, strictness reads the free-form
    criterion text. So one appended entry naming an invented criterion and the
    agent's own failing test bought a passing verdict, and it scaled to an
    arbitrarily red build.
    """

    async def test_an_invented_criterion_does_not_forgive_its_test(
        self, tmp_path: Path
    ) -> None:
        base = _workspace(tmp_path)
        _write(
            base,
            DEFAULT_MANIFEST_FILENAME,
            f"""\
language: python
test_command: pytest
test_report_path: {_REPORT}
pending:
  - criterion: deferred polish item
    test_id: tests/test_other.py::test_something_else
""",
        )
        _write(
            base,
            _REPORT,
            _report(_other_case('<failure message="assert 2 == 3"/>')),
        )

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is False

    async def test_an_invented_entry_alongside_a_real_one_breaks_the_run(
        self, tmp_path: Path
    ) -> None:
        """Not merely unforgiven: its test becomes an ordinary break.

        Declining to help would still leave the genuine pending failures
        forgivable, so the forged entry would cost the agent nothing. Read as
        an unrelated failure, the appended entry makes the whole run
        un-forgivable, which is what turns the forge into a refusal.
        """
        base = _workspace(tmp_path)
        _write(
            base,
            DEFAULT_MANIFEST_FILENAME,
            f"""\
language: python
test_command: pytest
test_report_path: {_REPORT}
pending:
  - criterion: a score is recorded
    test_id: {_TEST_ID}
  - criterion: deferred polish item
    test_id: tests/test_other.py::test_something_else
""",
        )
        _write(
            base,
            _REPORT,
            _report(
                _pending_case(_ASSERTION_FAILURE),
                _other_case('<failure message="assert 2 == 3"/>'),
            ),
        )

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=_RAN_AT
        )

        assert declared is False

    async def test_an_unknown_vocabulary_forgives_nothing(self, tmp_path: Path) -> None:
        """No plan to read is not permission; it is the absence of one."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case(_ASSERTION_FAILURE)))

        declared = failure_was_declared(
            await _contract(base), approved=frozenset(), not_before=_RAN_AT
        )

        assert declared is False


class TestTheReportMustSpeakForThisRun:
    """One report path is shared by every unit and every attempt.

    Nothing rewrites it when a run dies before producing one, so a unit whose
    suite timed out would otherwise have the skeleton's own leftover report
    read against it and its failing run forgiven.
    """

    async def test_a_report_older_than_the_run_is_not_evidence(
        self, tmp_path: Path
    ) -> None:
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case(_ASSERTION_FAILURE)))

        declared = failure_was_declared(
            await _contract(base),
            approved=_APPROVED,
            not_before=datetime.now(tz=UTC) + timedelta(hours=1),
        )

        assert declared is False

    async def test_asking_for_no_correlation_still_reads_the_report(
        self, tmp_path: Path
    ) -> None:
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)
        _write(base, _REPORT, _report(_pending_case(_ASSERTION_FAILURE)))

        declared = failure_was_declared(
            await _contract(base), approved=_APPROVED, not_before=None
        )

        assert declared is True


class TestWhichCriteriaAreStillUnclaimed:
    async def test_a_criterion_still_listed_is_reported(self, tmp_path: Path) -> None:
        """Clearing the entry in the same commit is the signal a unit is done.

        The suite exits zero either way, so nothing but this reading can catch
        a unit that implemented its criterion and left the marker for the next
        one to inherit.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert unclaimed_criteria(await _contract(base), [_CRITERION]) == (_CRITERION,)

    @pytest.mark.parametrize(
        "spelling",
        [
            pytest.param("a score is recorded", id="exact"),
            pytest.param("  A Score Is Recorded  ", id="case_and_whitespace"),
            pytest.param("A score is recorded.", id="trailing_full_stop"),
            pytest.param("a score is recorded!", id="trailing_exclamation"),
        ],
    )
    async def test_the_match_survives_a_respelling(
        self, tmp_path: Path, spelling: str
    ) -> None:
        """The task's wording and the manifest's have different authors.

        The criterion travels from the objective into a brief into a manifest
        entry an agent typed, so a comparison that misses on a full stop the
        agent dropped reports every unit as done, silently, for ever.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert unclaimed_criteria(await _contract(base), [spelling]) == (spelling,)

    async def test_another_units_criterion_is_not_this_units_problem(
        self, tmp_path: Path
    ) -> None:
        """Judged per criterion, never per project.

        A project mid-build always has other units' entries outstanding, and
        reading those would fail every unit until the last one.
        """
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, _MANIFEST)

        assert unclaimed_criteria(await _contract(base), ["something else"]) == ()


class TestWhetherTheContractCouldBeReadAtAll:
    """Three states, because they need three different answers."""

    @pytest.mark.parametrize(
        ("manifest", "expected"),
        [
            pytest.param(None, ContractState.ABSENT, id="no_manifest"),
            pytest.param(
                "language: [unclosed", ContractState.UNREADABLE, id="unparseable"
            ),
            pytest.param(
                "language: python\nnot_a_field: 1",
                ContractState.UNREADABLE,
                id="invalid",
            ),
            pytest.param(_MANIFEST, ContractState.READ, id="readable"),
        ],
    )
    async def test_the_state_says_which_it_was(
        self, tmp_path: Path, manifest: str | None, expected: ContractState
    ) -> None:
        """A broken manifest and an absent one are not the same fact.

        Collapsing them is what let one unparseable file waive the pending
        set, the clear-your-own-marker rule and every declared gate at once,
        under a verdict indistinguishable from a compliant project's.
        """
        base = _workspace(tmp_path)
        if manifest is not None:
            _write(base, DEFAULT_MANIFEST_FILENAME, manifest)

        assert (await _contract(base)).state is expected

    async def test_an_unreadable_contract_declares_no_gates(
        self, tmp_path: Path
    ) -> None:
        """The caller blocks on the state; these readers simply have nothing."""
        base = _workspace(tmp_path)
        _write(base, DEFAULT_MANIFEST_FILENAME, "language: [unclosed")

        contract = await _contract(base)

        assert declared_gates(contract) == {}
        assert unclaimed_criteria(contract, [_CRITERION]) == ()
