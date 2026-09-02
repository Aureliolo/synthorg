"""The reviewer reads recorded runs; it is never told to run anything."""

from datetime import UTC, datetime

import pytest

from synthorg.core.task_enums import Complexity, Stakes
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.prompt import (
    build_completion_reviewer_system_prompt,
    render_verification_runs,
)
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.prompt_safety import TAG_VERIFICATION_RUNS
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _run(
    *, purpose: CodeExecutionPurpose, returncode: int, stdout: str | None = None
) -> CodeExecutionRecord:
    return CodeExecutionRecord(
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        project_id=NotBlankStr("proj-1"),
        purpose=purpose,
        command=NotBlankStr("pytest -q"),
        returncode=returncode,
        passed=returncode == 0,
        timed_out=False,
        stdout_tail=stdout,
        executed_at=_AT,
    )


def _input(runs: tuple[CodeExecutionRecord, ...]) -> CompletionOracleReviewInput:
    return CompletionOracleReviewInput(
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        deliverable_content=NotBlankStr("def add(a, b): return a + b"),
        acceptance_criteria=(NotBlankStr("adds"),),
        executor_agent_id=NotBlankStr("executor-1"),
        stakes=Stakes.NORMAL,
        estimated_complexity=Complexity.MEDIUM,
        verification_runs=runs,
    )


class TestRenderVerificationRuns:
    def test_no_runs_says_nothing_is_proven(self) -> None:
        text = render_verification_runs(())
        assert "No build, test" in text
        assert "Nothing here proves" in text

    def test_each_run_carries_status_command_and_tail(self) -> None:
        text = render_verification_runs(
            (
                _run(purpose=CodeExecutionPurpose.TESTS, returncode=0, stdout="3 ok"),
                _run(purpose=CodeExecutionPurpose.LINT, returncode=1),
            )
        )
        assert "[tests] PASSED (exit 0)" in text
        assert "[lint] FAILED (exit 1)" in text
        assert "command: pytest -q" in text
        assert "stdout tail:\n3 ok" in text


class TestPromptCarriesTheEvidence:
    def test_runs_are_fenced_and_the_directive_names_the_tag(self) -> None:
        prompt = build_completion_reviewer_system_prompt(
            _input((_run(purpose=CodeExecutionPurpose.TESTS, returncode=0),))
        )
        assert f"<{TAG_VERIFICATION_RUNS}>" in prompt
        assert "[tests] PASSED" in prompt
        assert TAG_VERIFICATION_RUNS in prompt.split("<task-data>")[0]

    def test_the_reviewer_is_never_told_to_run_anything(self) -> None:
        prompt = build_completion_reviewer_system_prompt(_input(()))
        assert "code-execution tool to build it" not in prompt
        assert "run its tests yourself" not in prompt
        assert "you only read." in prompt
        assert "hold no shell" in prompt
