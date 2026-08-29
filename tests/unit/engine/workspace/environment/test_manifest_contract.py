"""What the committed manifest refuses to declare.

The manifest is the contract's durable half: it names the project's gates and
the criteria still pending. Every invariant here is one that fails silently if
it is not checked at parse time, because the readers downstream are pure
functions over whatever this model accepted.
"""

import pytest
from pydantic import ValidationError

from synthorg.engine.workspace.environment.manifest import (
    EnvironmentManifest,
    PendingTest,
)

pytestmark = pytest.mark.unit

_TEST_ID = "tests/test_score.py::test_a_score_is_recorded"


def _manifest(**overrides: object) -> EnvironmentManifest:
    """Build a valid manifest, overriding one field at a time.

    Returns:
        The manifest.
    """
    fields: dict[str, object] = {
        "language": "python",
        "setup_commands": ("uv sync",),
        "test_command": "pytest",
    }
    fields.update(overrides)
    return EnvironmentManifest(**fields)  # type: ignore[arg-type]


class TestAGateMustBeAskable:
    """A gate is one recorded exit status, so it has to be the gate's own."""

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("ruff check . || true", id="or_true"),
            pytest.param("ruff check . ; echo done", id="statement_separator"),
            pytest.param("ruff check . &", id="backgrounded"),
            pytest.param("# deferred", id="comment_runs_nothing"),
            pytest.param("sh -c ''", id="empty_shell_payload"),
        ],
    )
    def test_a_command_whose_status_says_nothing_is_refused(self, command: str) -> None:
        """Every run of it would mint a passing record for a gate that failed.

        Refused here because here is where a person is: the contract job's
        manifest goes through a review gate, so a declaration that cannot mean
        anything fails while somebody is looking at it.
        """
        with pytest.raises(ValidationError, match="exits zero whatever"):
            _manifest(lint_command=command)

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param("ruff check .", id="plain"),
            pytest.param("cd src && ruff check .", id="conjunctive"),
            pytest.param("ruff check . | tee lint.log", id="piped_under_pipefail"),
        ],
    )
    def test_a_command_that_still_speaks_for_itself_is_accepted(
        self, command: str
    ) -> None:
        assert _manifest(lint_command=command).lint_command == command


class TestThePendingSetMustBeMatchable:
    def test_an_unnormalised_criterion_is_refused(self) -> None:
        """The key is what both sides of the match compare on.

        An entry the normaliser would have changed matches nothing, and a check
        that never matches passes every unit silently.
        """
        with pytest.raises(ValidationError, match="is not normalised"):
            _manifest(
                test_report_path="junit.xml",
                pending=(
                    PendingTest(criterion="A Score Is Recorded", test_id=_TEST_ID),
                ),
            )

    def test_one_criterion_declared_twice_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="declared twice"):
            _manifest(
                test_report_path="junit.xml",
                pending=(
                    PendingTest(criterion="a score is recorded", test_id=_TEST_ID),
                    PendingTest(
                        criterion="a score is recorded", test_id="tests/t.py::other"
                    ),
                ),
            )

    def test_two_criteria_sharing_one_test_are_refused(self) -> None:
        """Neither could be cleared independently.

        The second unit to finish would find its marker already gone and read
        as done without having run.
        """
        with pytest.raises(ValidationError, match="claimed by two criteria"):
            _manifest(
                test_report_path="junit.xml",
                pending=(
                    PendingTest(criterion="a score is recorded", test_id=_TEST_ID),
                    PendingTest(criterion="a total is shown", test_id=_TEST_ID),
                ),
            )

    def test_pending_criteria_with_no_report_are_refused(self) -> None:
        """An exit status cannot classify them, so every one would read red."""
        with pytest.raises(ValidationError, match="need test_report_path"):
            _manifest(
                pending=(
                    PendingTest(criterion="a score is recorded", test_id=_TEST_ID),
                )
            )
