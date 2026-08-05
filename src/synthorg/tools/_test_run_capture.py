# module-kind: code
"""Was this execution a test run, and if so, record it.

The build/test oracle is a pure function of persisted ``CodeExecutionRecord``
rows. Which executions produce one therefore decides whether a deliverable
can be verified at all, and until now the answer came from a ``purpose``
argument the model filled in: an agent that ran its suite through
``shell_command``, or through ``code_runner`` without setting the flag,
produced a green suite and zero evidence, and the oracle correctly failed
closed on a build that genuinely passed.

Deciding from the command is also the safer design on its own terms. A
model-supplied flag is untrusted input, and letting untrusted input decide
whether a gate has evidence is backwards in both directions: it can withhold
evidence for a real run, and it can claim a trivial script was the suite.

Recognition is deliberately conservative. A command that runs a known test
runner is a test run; anything else is not, and produces no record. A false
positive would let a passing ``echo`` stand in for a suite, which is worse
than a false negative: an unrecognised runner reads as UNVERIFIED, which
blocks rather than passes.
"""

import re
from typing import Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import current_execution_identity
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    TEST_RUN_RECORD_FAILED,
    TEST_RUN_RECORDED,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)
from synthorg.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

#: Test runners recognised from the command text. Each entry matches the
#: invocation as a whole word, so ``pytest`` matches ``uv run pytest -q``
#: and ``python -m pytest`` alike, while ``pytest_helper.py`` does not.
_TEST_RUNNERS: Final[tuple[str, ...]] = (
    r"pytest",
    r"unittest",
    r"nox",
    r"tox",
    r"jest",
    r"vitest",
    r"mocha",
    r"rspec",
    r"phpunit",
    r"ctest",
    r"go\s+test",
    r"cargo\s+test",
    r"dotnet\s+test",
    r"mvn\s+(?:\S+\s+)*test",
    r"gradle\s+(?:\S+\s+)*test",
    r"make\s+(?:\S+\s+)*test",
    r"(?:npm|pnpm|yarn|bun|deno)\s+(?:run\s+)?test",
)

_TEST_COMMAND_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:" + "|".join(_TEST_RUNNERS) + r")\b",
    re.IGNORECASE,
)


def is_test_run(command: str) -> bool:
    """Whether *command* invokes a recognised test runner.

    Args:
        command: The full command line as it was executed.

    Returns:
        ``True`` when the command runs a test suite.
    """
    return bool(_TEST_COMMAND_RE.search(command))


async def record_if_test_run(
    result: SandboxResult,
    *,
    command: str,
    records: CodeExecutionRecordRepository | None,
    clock: Clock,
    command_repr_limit: int,
    output_tail_limit: int,
) -> None:
    """Persist *result* as test evidence when *command* ran a test suite.

    No-ops when the command is not a test run, when no repository is wired,
    or when called outside a bound execution scope. Best-effort: a capture
    failure logs and returns rather than failing the tool call, because
    losing the receipt must not lose the run.

    Args:
        result: The finished sandbox execution.
        command: The full command line, both classified and recorded.
        records: Append-only store for the receipt, or ``None`` when
            unwired.
        clock: Clock seam stamping the record.
        command_repr_limit: Characters of *command* kept on the record.
        output_tail_limit: Characters of stdout/stderr kept on the record.
    """
    if records is None or not is_test_run(command):
        return
    identity = current_execution_identity()
    if identity is None or identity.project_id is None:
        return
    try:
        await records.append(
            CodeExecutionRecord(
                task_id=identity.task_id,
                execution_id=identity.execution_id,
                project_id=identity.project_id,
                purpose=CodeExecutionPurpose.TESTS,
                command=command[:command_repr_limit],
                returncode=result.returncode,
                passed=result.success,
                timed_out=result.timed_out,
                stdout_tail=(
                    result.stdout[-output_tail_limit:] if result.stdout else None
                ),
                stderr_tail=(
                    result.stderr[-output_tail_limit:] if result.stderr else None
                ),
                executed_at=clock.now(),
            )
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- the receipt is a side channel; losing it
        # must never lose the run it describes.
        reraise_critical(exc)
        logger.warning(
            TEST_RUN_RECORD_FAILED,
            execution_id=identity.execution_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.debug(
        TEST_RUN_RECORDED,
        execution_id=identity.execution_id,
        task_id=identity.task_id,
        returncode=result.returncode,
        passed=result.success,
    )


__all__ = ["is_test_run", "record_if_test_run"]
