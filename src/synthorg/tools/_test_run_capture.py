# module-kind: code
"""Was this execution a test run, and if so, record it.

The build/test oracle is a pure function of persisted ``CodeExecutionRecord``
rows. Which executions produce one therefore decides whether a deliverable can
be verified at all, so it cannot be decided by a model-supplied ``purpose``
flag: an agent that runs its suite through ``shell_command``, or through
``code_runner`` without such a flag set, would produce a green suite and zero
evidence, and the oracle would correctly fail closed on a build that genuinely
passed.

Deciding from the command is also the safer design on its own terms. A
model-supplied flag is untrusted input, and letting untrusted input decide
whether a gate has evidence is backwards in both directions: it can withhold
evidence for a real run, and it can claim a trivial script was the suite.

The command is untrusted too, so recognition reads the *invoked program*,
never the line as text. A substring search would accept ``echo pytest``,
``cat pytest.ini`` and ``pip install pytest`` as passing suites, which is the
exact forgery this module exists to prevent: an agent whose suite failed could
run ``echo pytest`` and flip the oracle from blocked to verified.

A compound command is refused outright. ``pytest || true`` and
``pytest; echo done`` both exit 0 whatever the suite did, so the recorded
``passed`` would describe the tail rather than the tests. Refusing costs a
false negative, which reads as UNVERIFIED and blocks; accepting costs a false
positive, which passes.
"""

import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath, PureWindowsPath
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

#: Anything that makes the line's exit status stop being the runner's own,
#: or that hides a second command from the parse.
_COMPOUND_MARKERS: Final[tuple[str, ...]] = (
    ";",
    "&",
    "|",
    "`",
    "$(",
    ">",
    "<",
    "\n",
    "\r",
)

#: Prefixes that run another program without changing what is being run.
#: Each entry is matched then dropped, repeatedly, until the head is the
#: program itself: ``uv run python -m pytest`` reduces to ``pytest``.
_WRAPPERS: Final[tuple[tuple[str, ...], ...]] = (
    ("uv", "run"),
    ("uvx",),
    ("poetry", "run"),
    ("pipenv", "run"),
    ("hatch", "run"),
    ("pdm", "run"),
    ("rye", "run"),
    ("npx",),
    ("bunx",),
    ("pnpm", "dlx"),
    ("yarn", "dlx"),
    ("bundle", "exec"),
    ("dotnet", "tool", "run"),
    ("python", "-m"),
    ("python3", "-m"),
    ("py", "-m"),
    ("time",),
    ("nice",),
    ("xvfb-run",),
)

#: Programs whose invocation is itself a test run.
_DIRECT_RUNNERS: Final[frozenset[str]] = frozenset(
    {
        "pytest",
        "py.test",
        "unittest",
        "nox",
        "tox",
        "jest",
        "vitest",
        "mocha",
        "rspec",
        "phpunit",
        "ctest",
        "gotestsum",
    }
)

#: Programs that run tests only via a ``test`` subcommand appearing
#: immediately after the program name.
_IMMEDIATE_SUBCOMMAND_RUNNERS: Final[frozenset[str]] = frozenset(
    {"go", "cargo", "dotnet", "swift"}
)

#: Build tools that run tests via a ``test`` target anywhere among their
#: non-flag arguments: ``mvn clean test``, ``make -j4 test``. Their argument
#: lists are phase / target names, so a ``test`` among them always names the
#: test phase.
_TARGET_RUNNERS: Final[frozenset[str]] = frozenset({"mvn", "gradle", "gradlew", "make"})

#: Package managers, whose first non-flag argument is a verb. ``test`` counts
#: only as that verb, or as the script named by ``run``: ``npm install test``
#: and ``yarn add test`` install a package that happens to be called ``test``
#: and would otherwise mint passing test evidence for a command that ran no
#: tests, which is exactly the forgery this evidence exists to resist.
_PACKAGE_MANAGER_RUNNERS: Final[frozenset[str]] = frozenset(
    {"npm", "pnpm", "yarn", "bun", "deno"}
)

_TEST_TARGET: Final[str] = "test"
_RUN_SUBCOMMAND: Final[str] = "run"

#: Shells whose ``-c`` argument is itself a command line, so the question
#: recurses into it once. ``bash -c "pytest -q"`` really did run the suite,
#: and its exit status is the suite's.
_SHELLS: Final[frozenset[str]] = frozenset({"bash", "sh", "zsh", "dash", "ash"})
_SHELL_COMMAND_FLAG: Final[str] = "-c"
#: ``<shell> -c <one command string>`` and nothing else.
_SHELL_INVOCATION_TOKENS: Final[int] = 3


def _program_name(token: str) -> str:
    """Reduce an argv head to the bare program name.

    Returns:
        The lowercased basename with any ``.exe`` suffix removed, so an
        absolute or Windows-style path resolves to the same name a bare
        invocation would.
    """
    name = PurePosixPath(PureWindowsPath(token).name).name.lower()
    return name.removesuffix(".exe")


def _strip_wrappers(tokens: Sequence[str]) -> tuple[str, ...]:
    """Drop leading environment assignments and wrapper invocations.

    Returns:
        The remaining tokens, whose head is the program actually being run.
    """
    remaining = tuple(tokens)
    changed = True
    while changed and remaining:
        changed = False
        head = _program_name(remaining[0])
        if "=" in remaining[0] and not remaining[0].startswith("="):
            remaining = remaining[1:]
            changed = True
            continue
        if head == "env":
            remaining = remaining[1:]
            changed = True
            continue
        for wrapper in _WRAPPERS:
            if len(remaining) > len(wrapper) and all(
                _program_name(remaining[index]) == part
                for index, part in enumerate(wrapper)
            ):
                remaining = remaining[len(wrapper) :]
                changed = True
                break
    return remaining


def is_test_run(command: str, *, _shell_depth: int = 0) -> bool:
    """Whether *command* invokes a recognised test runner.

    Args:
        command: The full command line as it was executed.
        _shell_depth: Recursion guard for a shell's ``-c`` payload, which is
            itself a command line. One level only; a shell invoking a shell
            is not a shape this needs to recognise.

    Returns:
        ``True`` only when the invoked program is a test runner and the
        line's exit status is that runner's own.
    """
    if any(marker in command for marker in _COMPOUND_MARKERS):
        return False
    try:
        parsed = shlex.split(command)
    except ValueError:
        return False
    tokens = _strip_wrappers(parsed)
    if not tokens:
        return False
    program = _program_name(tokens[0])
    if (
        program in _SHELLS
        and _shell_depth == 0
        and len(tokens) == _SHELL_INVOCATION_TOKENS
        and tokens[1] == _SHELL_COMMAND_FLAG
    ):
        return is_test_run(tokens[2], _shell_depth=1)
    if program in _DIRECT_RUNNERS:
        return True
    arguments = tuple(token for token in tokens[1:] if not token.startswith("-"))
    if program in _IMMEDIATE_SUBCOMMAND_RUNNERS:
        return bool(arguments) and arguments[0] == _TEST_TARGET
    if program in _PACKAGE_MANAGER_RUNNERS:
        return _is_package_manager_test(arguments)
    if program in _TARGET_RUNNERS:
        return _TEST_TARGET in arguments
    return False


def _is_package_manager_test(arguments: tuple[str, ...]) -> bool:
    """Whether a package manager's *arguments* invoke its test script.

    Args:
        arguments: The non-flag arguments following the program name.

    Returns:
        ``True`` for ``test`` as the verb (``npm test``) or as the script
        ``run`` names (``pnpm run test``), and ``False`` for every other
        verb, so ``npm install test`` is an install and not a test run.
    """
    if not arguments:
        return False
    if arguments[0] == _TEST_TARGET:
        return True
    return (
        arguments[0] == _RUN_SUBCOMMAND
        and len(arguments) > 1
        and arguments[1] == _TEST_TARGET
    )


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
        command: The command line, both classified and recorded. It must be
            the invocation alone: a caller that appends a code snippet would
            have the snippet's text decide the classification.
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
        # must never lose the run it describes. Losing one can only withhold
        # evidence, and the oracle reads missing evidence as UNVERIFIED.
        reraise_critical(exc)
        logger.warning(
            TEST_RUN_RECORD_FAILED,
            execution_id=identity.execution_id,
            task_id=identity.task_id,
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
