"""Tests for the executable brief grader."""

import sys
from pathlib import Path

import pytest

from evals.errors import EvalToolMissingError
from evals.models.brief import (
    Brief,
    BriefKind,
    BriefPriority,
    ExecutableChecks,
    HiddenCheckSpec,
    LimitsSpec,
)
from evals.scoring.executable import (
    EXEC_TOTAL,
    EXEC_WEIGHT_BUILD,
    EXEC_WEIGHT_HIDDEN,
    EXEC_WEIGHT_LINT,
    grade_executable,
)


def _exec_brief(
    *,
    hidden: tuple[HiddenCheckSpec, ...] = (),
    build: tuple[HiddenCheckSpec, ...] = (),
    lint: tuple[HiddenCheckSpec, ...] = (),
) -> Brief:
    return Brief(
        brief_id="BRIEF_TEST",
        schema_version=1,
        kind=BriefKind.EXECUTABLE,
        title="Test brief",
        description="A brief used in grader tests.",
        priority=BriefPriority.MEDIUM,
        estimated_complexity=2,
        acceptance_criteria=("c1",),
        limits=LimitsSpec(
            max_total_cost_usd=1.0,
            max_wall_clock_seconds=30,
            max_turns=4,
        ),
        checks=ExecutableChecks(
            hidden_tests=hidden,
            build=build,
            lint=lint,
        ),
    )


def _cmd_true() -> HiddenCheckSpec:
    # Cross-platform "exit 0".
    return HiddenCheckSpec(
        cmd=(sys.executable, "-c", "import sys; sys.exit(0)"),
        timeout_seconds=10,
    )


def _cmd_false() -> HiddenCheckSpec:
    return HiddenCheckSpec(
        cmd=(sys.executable, "-c", "import sys; sys.exit(2)"),
        timeout_seconds=10,
    )


def _cmd_sleep() -> HiddenCheckSpec:
    # Sleep longer than the timeout to test timed_out path.
    return HiddenCheckSpec(
        cmd=(sys.executable, "-c", "import time; time.sleep(5)"),
        timeout_seconds=1,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("hidden_pass", "build_pass", "lint_pass", "expected_score"),
    [
        (True, True, True, EXEC_TOTAL),
        (False, True, True, EXEC_TOTAL - EXEC_WEIGHT_HIDDEN),
        (True, False, True, EXEC_TOTAL - EXEC_WEIGHT_BUILD),
        (True, True, False, EXEC_TOTAL - EXEC_WEIGHT_LINT),
        (False, True, False, EXEC_TOTAL - EXEC_WEIGHT_HIDDEN - EXEC_WEIGHT_LINT),
        (False, False, False, 0),
    ],
    ids=[
        "all_pass",
        "hidden_fail",
        "build_fail",
        "lint_fail",
        "hidden_and_lint_fail",
        "all_fail",
    ],
)
def test_grade_weights_by_class(
    tmp_path: Path,
    hidden_pass: bool,
    build_pass: bool,
    lint_pass: bool,
    expected_score: int,
) -> None:
    """One row per pass/fail combination across hidden / build / lint."""
    brief = _exec_brief(
        hidden=(_cmd_true() if hidden_pass else _cmd_false(),),
        build=(_cmd_true() if build_pass else _cmd_false(),),
        lint=(_cmd_true() if lint_pass else _cmd_false(),),
    )
    grade = grade_executable(brief, tmp_path)
    assert grade.score == expected_score
    assert grade.hidden_pass is hidden_pass
    assert grade.build_pass is build_pass
    assert grade.lint_pass is lint_pass


@pytest.mark.unit
def test_empty_check_class_contributes_full_weight(tmp_path: Path) -> None:
    # No build commands declared -> build_pass is True -> weight credited.
    brief = _exec_brief(hidden=(_cmd_true(),))
    grade = grade_executable(brief, tmp_path)
    assert grade.score == EXEC_TOTAL
    assert grade.build_pass is True
    assert grade.lint_pass is True


@pytest.mark.unit
def test_timeout_is_treated_as_failure(tmp_path: Path) -> None:
    brief = _exec_brief(hidden=(_cmd_sleep(),))
    grade = grade_executable(brief, tmp_path)
    assert grade.hidden_pass is False
    assert grade.outcomes[0].timed_out is True


@pytest.mark.unit
def test_missing_tool_raises(tmp_path: Path) -> None:
    brief = _exec_brief(
        hidden=(
            HiddenCheckSpec(
                cmd=("definitely-not-a-real-binary-xyz",), timeout_seconds=5
            ),
        ),
    )
    with pytest.raises(EvalToolMissingError):
        grade_executable(brief, tmp_path)


@pytest.mark.unit
def test_check_outcome_timeout_must_use_posix_sentinel() -> None:
    """CheckOutcome refuses timed_out=True with a non-sentinel exit code."""
    from pydantic import ValidationError as PydValidationError

    from evals.scoring.executable import CheckOutcome

    with pytest.raises(PydValidationError, match="POSIX timeout sentinel"):
        CheckOutcome(
            label="hidden",
            cmd=("x",),
            exit_code=1,  # not 124
            duration_seconds=0.0,
            stdout_tail="",
            stderr_tail="",
            timed_out=True,
        )


@pytest.mark.unit
def test_executable_grade_pass_flags_must_match_outcomes() -> None:
    """ExecutableGrade refuses pass-flags that contradict the outcomes."""
    from pydantic import ValidationError as PydValidationError

    from evals.scoring.executable import (
        EXEC_WEIGHT_HIDDEN,
        CheckOutcome,
        ExecutableGrade,
    )

    failing_hidden = CheckOutcome(
        label="hidden",
        cmd=("x",),
        exit_code=2,
        duration_seconds=0.0,
        stdout_tail="",
        stderr_tail="",
    )
    with pytest.raises(PydValidationError, match="do not match"):
        ExecutableGrade(
            score=EXEC_WEIGHT_HIDDEN,  # invalid combo for hidden=True
            hidden_pass=True,
            build_pass=True,
            lint_pass=True,
            outcomes=(failing_hidden,),
        )


@pytest.mark.unit
def test_wrong_kind_rejected(tmp_path: Path) -> None:
    # Build a judged brief and try to grade it as executable -> rejected.
    from evals.models.brief import (
        JudgedRubric,
        RubricDimension,
        RubricGradeType,
    )

    judged = Brief(
        brief_id="BRIEF_J",
        schema_version=1,
        kind=BriefKind.JUDGED,
        title="t",
        description="d",
        priority=BriefPriority.LOW,
        estimated_complexity=1,
        acceptance_criteria=("c",),
        limits=LimitsSpec(
            max_total_cost_usd=1.0,
            max_wall_clock_seconds=30,
            max_turns=4,
        ),
        rubric=JudgedRubric(
            rubric_id="r",
            dimensions=(
                RubricDimension(
                    name="a", weight=1.0, grade_type=RubricGradeType.TERNARY
                ),
            ),
            reference_answer_path="anchors/r.md",
        ),
    )
    with pytest.raises(ValueError, match="kind="):
        grade_executable(judged, tmp_path)
