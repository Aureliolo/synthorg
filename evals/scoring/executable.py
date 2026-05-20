"""Binary grading for ``kind=executable`` briefs.

Every executable brief ships hidden acceptance tests, build, and lint
commands. The grader runs each command class independently and assigns
a weighted-sum score in ``[0, 100]``: 60 for hidden tests, 20 for
build, 20 for lint. The math is intentionally simple and the
constants are named in :data:`EXEC_WEIGHT_*` so a tuning change is
one file edit.

Commands run via :func:`subprocess.run` with ``shell=False``. A
missing executable on PATH raises :class:`EvalToolMissingError`
because the eval itself is broken; the brief is not scored. Per-
command timeouts surface as failed outcomes (non-zero exit), keeping
the brief's failure attributable.
"""

import subprocess
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from evals.errors import EvalToolMissingError
from evals.models.brief import Brief, BriefKind, ExecutableChecks, HiddenCheckSpec
from synthorg.observability import get_logger, safe_error_description

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = get_logger(__name__)

# Weighted contribution of each check class to a brief's grade. Sum to
# EXEC_TOTAL; tuning is intentional and goes here, not in YAML.
EXEC_TOTAL: Final[int] = 100
EXEC_WEIGHT_HIDDEN: Final[int] = 60
EXEC_WEIGHT_BUILD: Final[int] = 20
EXEC_WEIGHT_LINT: Final[int] = 20

# Cap on captured stdout/stderr per command. Larger output is silently
# truncated to keep scorecards a reviewable size; the truncation marker
# below is appended so a reader knows the tail was dropped.
OUTPUT_TAIL_BYTES: Final[int] = 512
OUTPUT_TRUNCATED_MARKER: Final[str] = "...[truncated]"


class CheckOutcome(BaseModel):
    """The result of running one subprocess command in a check class."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    label: str
    cmd: tuple[str, ...]
    exit_code: int
    duration_seconds: float = Field(ge=0.0)
    stdout_tail: str
    stderr_tail: str
    timed_out: bool = False


class ExecutableGrade(BaseModel):
    """Aggregate grade across hidden + build + lint check classes."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    score: int = Field(ge=0, le=EXEC_TOTAL)
    hidden_pass: bool
    build_pass: bool
    lint_pass: bool
    outcomes: tuple[CheckOutcome, ...]

    @property
    def is_clean(self) -> bool:
        """Whether every declared check class passed."""
        return self.hidden_pass and self.build_pass and self.lint_pass


def _tail(payload: bytes) -> str:
    """Decode the trailing bytes of *payload* with a truncation marker."""
    if len(payload) <= OUTPUT_TAIL_BYTES:
        return payload.decode("utf-8", errors="replace")
    tail = payload[-OUTPUT_TAIL_BYTES:].decode("utf-8", errors="replace")
    return f"{OUTPUT_TRUNCATED_MARKER}\n{tail}"


def _run_check(spec: HiddenCheckSpec, label: str, work_dir: Path) -> CheckOutcome:
    """Run one check; never raises on non-zero exit; raises on tool-missing.

    Returns:
        :class:`CheckOutcome`. ``timed_out`` is True when the command
        exceeded ``spec.timeout_seconds``; in that case ``exit_code``
        is set to a non-zero sentinel so downstream scoring treats it
        as a failure.

    Raises:
        EvalToolMissingError: If ``spec.cmd[0]`` cannot be launched
            (FileNotFoundError / NotADirectoryError). Sharp failure:
            the eval cannot grade a brief whose tools are not
            installed.
    """
    try:
        completed = subprocess.run(  # noqa: S603 -- args validated, no shell
            list(spec.cmd),
            cwd=work_dir,
            timeout=spec.timeout_seconds,
            capture_output=True,
            check=False,
            shell=False,
        )
        return CheckOutcome(
            label=label,
            cmd=spec.cmd,
            exit_code=completed.returncode,
            duration_seconds=0.0,
            stdout_tail=_tail(completed.stdout),
            stderr_tail=_tail(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "evals.executable.timeout",
            cmd=spec.cmd,
            timeout=spec.timeout_seconds,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return CheckOutcome(
            label=label,
            cmd=spec.cmd,
            exit_code=124,  # POSIX-conventional timeout exit code
            duration_seconds=float(spec.timeout_seconds),
            stdout_tail=_tail(exc.stdout or b""),
            stderr_tail=_tail(exc.stderr or b""),
            timed_out=True,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        logger.error(
            "evals.executable.tool_missing",
            cmd=spec.cmd,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Required eval tool not found: {spec.cmd[0]!r}"
        raise EvalToolMissingError(msg) from exc


def _all_pass(outcomes: Iterable[CheckOutcome]) -> bool:
    """True if every outcome carried a zero exit code AND did not time out."""
    outs = tuple(outcomes)
    if not outs:
        return True
    return all(o.exit_code == 0 and not o.timed_out for o in outs)


def grade_executable(brief: Brief, work_dir: Path) -> ExecutableGrade:
    """Run every check in *brief.checks* and assemble a weighted grade.

    Args:
        brief: An executable brief (``kind=executable``); raises if
            the brief is not executable. This is an internal contract
            violation, not user input.
        work_dir: Directory used as ``cwd`` for every subprocess call.

    Returns:
        :class:`ExecutableGrade` with weighted score and per-command
        outcomes. ``score`` is the sum of contributions for each check
        class that fully passed; a class with no commands contributes
        its full weight (a brief that asserts only hidden tests, for
        instance, still scores 100 when those hidden tests pass).

    Raises:
        ValueError: If *brief.kind* is not ``EXECUTABLE``.
        EvalToolMissingError: Propagated from :func:`_run_check`.
    """
    if brief.kind is not BriefKind.EXECUTABLE:
        msg = f"grade_executable called with kind={brief.kind.value!r}"
        raise ValueError(msg)
    if brief.checks is None:
        msg = "executable brief is missing its 'checks' block"
        raise ValueError(msg)

    checks: ExecutableChecks = brief.checks
    hidden_outcomes = tuple(
        _run_check(spec, label="hidden", work_dir=work_dir)
        for spec in checks.hidden_tests
    )
    build_outcomes = tuple(
        _run_check(spec, label="build", work_dir=work_dir) for spec in checks.build
    )
    lint_outcomes = tuple(
        _run_check(spec, label="lint", work_dir=work_dir) for spec in checks.lint
    )

    hidden_pass = _all_pass(hidden_outcomes)
    build_pass = _all_pass(build_outcomes)
    lint_pass = _all_pass(lint_outcomes)

    score = (
        (EXEC_WEIGHT_HIDDEN if hidden_pass else 0)
        + (EXEC_WEIGHT_BUILD if build_pass else 0)
        + (EXEC_WEIGHT_LINT if lint_pass else 0)
    )

    return ExecutableGrade(
        score=score,
        hidden_pass=hidden_pass,
        build_pass=build_pass,
        lint_pass=lint_pass,
        outcomes=hidden_outcomes + build_outcomes + lint_outcomes,
    )


__all__ = [
    "EXEC_TOTAL",
    "EXEC_WEIGHT_BUILD",
    "EXEC_WEIGHT_HIDDEN",
    "EXEC_WEIGHT_LINT",
    "OUTPUT_TAIL_BYTES",
    "OUTPUT_TRUNCATED_MARKER",
    "CheckOutcome",
    "ExecutableGrade",
    "grade_executable",
]
