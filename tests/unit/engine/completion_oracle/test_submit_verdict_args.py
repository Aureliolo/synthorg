# module-kind: tests
"""The verdict tool reads "no command" however a model spells it.

A live reviewer sent the text ``null`` for the optional test command and was
refused twice for naming a command called null. A refusal is retryable, but a
turn spent on a spelling is a turn, and nothing anybody runs is called null.
"""

import pytest

from synthorg.engine.completion_oracle.review_models import CompletionOracleVerdict
from synthorg.engine.completion_oracle.tools._args import (
    SubmitCompletionOracleVerdictArgs,
)

pytestmark = pytest.mark.unit


def _args(**overrides: object) -> SubmitCompletionOracleVerdictArgs:
    payload: dict[str, object] = {
        "execution_id": "exec-1",
        "task_id": "task-1",
        "verdict": CompletionOracleVerdict.APPROVE,
        "summary": "Clean.",
        **overrides,
    }
    return SubmitCompletionOracleVerdictArgs.model_validate(payload)


class TestTheSchemaSaysWhichFieldTheBriefReads:
    """Every round's first submission put its findings in the summary.

    The refusal taught the model one turn late; the schema the model reads
    before calling is where the rule has to be.
    """

    def test_findings_description_names_the_rework_brief(self) -> None:
        schema = SubmitCompletionOracleVerdictArgs.model_json_schema()
        findings = schema["properties"]["findings"]["description"]
        summary = schema["properties"]["summary"]["description"]
        assert "rework brief reads this list" in findings
        assert "never the summary" in findings
        assert "Not read by the rework brief" in summary


class TestAnAbsentCommandHasManySpellings:
    @pytest.mark.parametrize("spelling", ["null", "None", "NULL", "", "  "])
    def test_the_null_spellings_read_as_no_command(self, spelling: str) -> None:
        assert _args(test_command=spelling).test_command is None

    def test_a_real_command_is_kept_verbatim(self) -> None:
        assert _args(test_command="pytest -q").test_command == "pytest -q"

    def test_json_null_still_reads_as_no_command(self) -> None:
        assert _args(test_command=None).test_command is None
