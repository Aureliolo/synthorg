"""Unit tests for pending-criterion classification.

The five-way table is the whole point of the module, so it is parametrised
row by row: every row is a separate way a test can end, and four of the five
have to stay red or a skeleton that never loaded ships as a green trunk.
"""

from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.manifest import PendingTest
from synthorg.engine.workspace.environment.pending import (
    PendingVerdict,
    classify_pending,
)

pytestmark = pytest.mark.unit

_TEST_ID = "tests/test_score.py::test_a_score_is_recorded"
_CRITERION = "a score is recorded"
_REPORT = "reports/junit.xml"


def _pending() -> tuple[PendingTest, ...]:
    """The one-criterion pending set every case below classifies.

    Returns:
        A single pending declaration.
    """
    return (
        PendingTest(
            criterion=NotBlankStr(_CRITERION),
            test_id=NotBlankStr(_TEST_ID),
        ),
    )


def _write_report(workspace: Path, body: str) -> None:
    """Put a JUnit report at the manifest-declared path under *workspace*."""
    report = workspace / _REPORT
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(body, encoding="utf-8")


def _case(outcome: str) -> str:
    """Build a one-case JUnit document whose case ends with *outcome*.

    Args:
        outcome: The XML for the outcome child, empty for a pass.

    Returns:
        The report body.
    """
    return (
        '<testsuite name="pytest" tests="1">'
        '<testcase classname="tests.test_score" file="tests/test_score.py" '
        f'name="test_a_score_is_recorded">{outcome}</testcase>'
        "</testsuite>"
    )


class TestTheFiveWayTable:
    """Only the declared assertion failure is green.

    Each other row is its own reason a skeleton is not merely unimplemented,
    and collapsing any of them into the green row turns the pending marker
    from a contract into a mute button.
    """

    @pytest.mark.parametrize(
        ("outcome", "verdict", "reason_fragment"),
        [
            pytest.param(
                '<failure message="assert 0 == 1">assert 0 == 1</failure>',
                PendingVerdict.GREEN,
                "declared assertion",
                id="declared_assertion_failure",
            ),
            pytest.param(
                '<error message="ImportError">No module named scoring</error>',
                PendingVerdict.RED,
                "raised before it could assert",
                id="collection_error",
            ),
            pytest.param(
                '<failure message="KeyError: total">KeyError: total</failure>',
                PendingVerdict.RED,
                "raised rather than asserting",
                id="unexpected_exception",
            ),
            pytest.param(
                '<skipped message="no backend"/>',
                PendingVerdict.RED,
                "was skipped",
                id="skipped",
            ),
            pytest.param(
                "",
                PendingVerdict.RED,
                "clear its manifest entry",
                id="passed_while_pending",
            ),
        ],
    )
    def test_each_outcome_gets_its_own_verdict(
        self,
        tmp_path: Path,
        outcome: str,
        verdict: PendingVerdict,
        reason_fragment: str,
    ) -> None:
        _write_report(tmp_path, _case(outcome))

        report = classify_pending(
            _pending(), workspace_path=tmp_path, test_report_path=_REPORT
        )

        assert report.report_read is True
        assert [entry.verdict for entry in report.outcomes] == [verdict]
        assert reason_fragment in report.outcomes[0].reason
        assert report.green is (verdict is PendingVerdict.GREEN)

    @pytest.mark.parametrize(
        ("message", "verdict"),
        [
            pytest.param("assert 0 == 1", PendingVerdict.GREEN, id="bare_assert"),
            pytest.param(
                "AssertionError: not implemented",
                PendingVerdict.GREEN,
                id="assertion_error",
            ),
            pytest.param(
                "expect(received).toBe(expected)",
                PendingVerdict.GREEN,
                id="jest_expect",
            ),
            pytest.param("KeyError: 'total'", PendingVerdict.RED, id="key_error"),
            pytest.param(
                "TypeError: unsupported operand",
                PendingVerdict.RED,
                id="type_error",
            ),
            pytest.param("", PendingVerdict.RED, id="no_message"),
        ],
    )
    def test_a_failure_is_read_from_its_message_not_its_tag(
        self, tmp_path: Path, message: str, verdict: PendingVerdict
    ) -> None:
        """A runner picks ``failure`` over ``error`` by phase, not by cause.

        pytest writes ``failure`` for anything that reaches the test body, so a
        pending test raising ``KeyError`` is tagged exactly as a lost assertion
        is. Trusting the tag alone forgives a skeleton that crashes, which is
        the one outcome the marker must never cover.
        """
        _write_report(tmp_path, _case(f'<failure message="{message}"/>'))

        report = classify_pending(
            _pending(), workspace_path=tmp_path, test_report_path=_REPORT
        )

        assert report.outcomes[0].verdict is verdict
        assert report.green is (verdict is PendingVerdict.GREEN)

    def test_a_test_the_report_never_names_is_red(self, tmp_path: Path) -> None:
        """A timeout or a runner crash looks exactly like this from here.

        The runner died before writing the case, so nothing was measured. The
        report parses, so the fail-closed path below does not cover it: the
        absent case has to be red on its own.
        """
        _write_report(tmp_path, '<testsuite name="pytest" tests="0"></testsuite>')

        report = classify_pending(
            _pending(), workspace_path=tmp_path, test_report_path=_REPORT
        )

        assert report.outcomes[0].verdict is PendingVerdict.RED
        assert "names no such test" in report.outcomes[0].reason


class TestWhenNothingCanBeRead:
    """A report that cannot be read measured nothing, and says so.

    Reported apart from a criterion that was measured and lost, because the
    two demand opposite responses: one is a rework round on the contract, the
    other is a broken runner nobody has noticed.
    """

    @pytest.mark.parametrize(
        ("declared_path", "body"),
        [
            pytest.param(None, None, id="no_report_declared"),
            pytest.param(_REPORT, None, id="report_missing"),
            pytest.param(_REPORT, "<testsuite><testcase", id="report_unparseable"),
            pytest.param("../escaped.xml", None, id="report_outside_workspace"),
        ],
    )
    def test_every_criterion_is_red_and_flagged_unread(
        self, tmp_path: Path, declared_path: str | None, body: str | None
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        if body is not None:
            _write_report(workspace, body)

        report = classify_pending(
            _pending(), workspace_path=workspace, test_report_path=declared_path
        )

        assert report.report_read is False
        assert report.green is False
        assert [entry.verdict for entry in report.outcomes] == [PendingVerdict.RED]

    def test_a_report_outside_the_workspace_is_never_opened(
        self, tmp_path: Path
    ) -> None:
        """The manifest is committed content an agent writes, so its path is
        untrusted input: a relative escape must be refused rather than followed,
        or a green verdict can be bought with a file the agent never ran.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (tmp_path / "escaped.xml").write_text(
            _case('<failure message="assert 0 == 1"/>'), encoding="utf-8"
        )

        report = classify_pending(
            _pending(), workspace_path=workspace, test_report_path="../escaped.xml"
        )

        assert report.report_read is False
        assert report.outcomes[0].verdict is PendingVerdict.RED


class TestAnEmptyPendingSet:
    def test_declares_nothing_outstanding_rather_than_nothing_measured(
        self, tmp_path: Path
    ) -> None:
        """Green with no report, which is not the same claim as a lost report.

        A skeleton whose criteria are all implemented has no pending entries
        left, and asking it for a report it has no reason to declare would fail
        every finished project.
        """
        report = classify_pending((), workspace_path=tmp_path, test_report_path=None)

        assert report.outcomes == ()
        assert report.report_read is True
        assert report.green is True


class TestHowARunnerSpellsTheNodeId:
    """Runners disagree on where the file goes, and the manifest may use either.

    A skeleton whose manifest names the test one way while the runner writes it
    the other reads as "the report names no such test", which is red, so an
    author is sent to fix a contract that is correct.

    The node id is the spelling that matters most, because it is the one the
    manifest is documented to carry and the one no classname-derived form can
    produce: pytest writes the classname as a dotted module path and keeps the
    file in its own attribute, so the two share no boundary to build it from.
    """

    @pytest.mark.parametrize(
        "test_id",
        [
            pytest.param("test_a_score_is_recorded", id="bare_name"),
            pytest.param(
                "tests/test_score.py::test_a_score_is_recorded", id="pytest_node_id"
            ),
            pytest.param(
                "tests.test_score::test_a_score_is_recorded", id="classname_qualified"
            ),
            pytest.param("tests.test_score.test_a_score_is_recorded", id="dotted"),
        ],
    )
    def test_every_spelling_reaches_the_same_case(
        self, tmp_path: Path, test_id: str
    ) -> None:
        _write_report(tmp_path, _case('<failure message="assert 0 == 1"/>'))

        report = classify_pending(
            (
                PendingTest(
                    criterion=NotBlankStr(_CRITERION),
                    test_id=NotBlankStr(test_id),
                ),
            ),
            workspace_path=tmp_path,
            test_report_path=_REPORT,
        )

        assert report.green is True

    def test_a_method_test_keeps_its_class_hop(self, tmp_path: Path) -> None:
        """``classname`` carries the class beyond the module the file names.

        Dropping that segment would build ``file::name`` for a method test,
        which is not what the runner calls it, so the manifest entry a skeleton
        actually writes would match nothing.
        """
        _write_report(
            tmp_path,
            '<testsuite name="pytest" tests="1">'
            '<testcase classname="tests.test_score.TestScore" '
            'file="tests/test_score.py" name="test_a_score_is_recorded">'
            '<failure message="assert 0 == 1"/>'
            "</testcase></testsuite>",
        )

        report = classify_pending(
            (
                PendingTest(
                    criterion=NotBlankStr(_CRITERION),
                    test_id=NotBlankStr(
                        "tests/test_score.py::TestScore::test_a_score_is_recorded"
                    ),
                ),
            ),
            workspace_path=tmp_path,
            test_report_path=_REPORT,
        )

        assert report.green is True
