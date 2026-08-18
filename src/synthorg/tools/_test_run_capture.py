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

What a compound command is judged on is whether the line's exit status
still implies the runner's own. ``pytest || true`` and ``pytest; echo done``
both exit 0 whatever the suite did, so they are refused: the recorded
``passed`` would describe the tail rather than the tests.

``&&`` and ``|`` are different, and refusing them cost the gate everything
it was for. A line built only of those two exits zero only when EVERY
command in it exited zero: ``&&`` short-circuits by definition, and ``|``
does the same because :mod:`synthorg.tools._shell_invocation` runs every
agent line under ``pipefail``. So ``cd /workspace && npm test 2>&1 | tail``
is exactly as trustworthy as a bare ``npm test``, and it is the shape agents
actually type. Refusing it meant a live run produced 181 shell commands,
several genuinely green suites, and zero evidence, and the oracle correctly
blocked every one of them for a build that passed.

That theorem is about the shell WE start, so it stops at the first shell the
line starts itself: ``pipefail`` is a shell option and a fresh shell does not
inherit it. Inside a ``bash -c`` payload a pipeline is therefore back to
reporting its last command's status, and ``|`` is refused there.

Redirections are noise: they move file descriptors and leave the exit status
alone. Command substitution, backgrounding and subshells are refused, since
each can run a program the parse never sees. A statement separator is refused
against the raw line rather than the token stream, because :mod:`shlex` lists
newline in its whitespace and hands back tokens with the separator already
eaten: a line running ``pytest -q``, then a newline, then ``echo ok`` would
otherwise read as one command headed by the runner, while the status recorded
for it is the one ``echo`` exited with.
"""

import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
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

#: Operators joining commands whose statuses the line's status still
#: implies: ``&&`` short-circuits, and ``|`` is conjunctive under the
#: ``pipefail`` every agent line runs with.
_CONJUNCTIVE_SEPARATORS: Final[frozenset[str]] = frozenset({"&&", "|"})

#: Operators that make the line's exit status stop being the runner's own
#: (``;``, ``||``, backgrounding) or that run a program the parse never
#: sees (subshells, substitution).
_STATUS_MASKING_TOKENS: Final[frozenset[str]] = frozenset(
    {";", ";;", "||", "&", "|&", "(", ")", "$", "{", "}"}
)

#: Redirection operators. They move file descriptors and leave the exit
#: status alone, so both the operator and its target are dropped.
_REDIRECTIONS: Final[frozenset[str]] = frozenset(
    {">", ">>", ">|", ">&", "<", "<<", "<<<", "<&", "&>", "&>>"}
)

#: Characters no token may contain. A backtick runs a command the parse
#: never sees. Statement separators are NOT here: :mod:`shlex` lists them
#: in ``whitespace``, so it consumes them as token boundaries and no token
#: can ever hold one. They are checked against the raw line instead, by
#: :data:`_STATEMENT_SEPARATORS`.
_FORBIDDEN_IN_TOKEN: Final[tuple[str, ...]] = ("`",)

#: Characters that end a statement, checked against the unlexed line.
#: A second statement's exit status is the line's, so ``pytest -q\necho ok``
#: reports the status of ``echo``: the runner could have failed and the
#: line still exits zero, which is a passing record for a red suite.
_STATEMENT_SEPARATORS: Final[tuple[str, ...]] = ("\n", "\r")

#: The pipe, whose conjunctive reading holds only under ``pipefail``.
_PIPE: Final[str] = "|"

#: The builtin that toggles ``pipefail``, and the signs that do it.
#: ``set -o`` enables, ``set +o`` disables, which is the opposite of the
#: convention most flags follow.
_SET_BUILTIN: Final[str] = "set"
_UNSET_SIGN: Final[str] = "+"
_SET_SIGN: Final[str] = "-"
#: The letter ``-o`` / ``+o`` ends with. Read as the LAST character of the
#: token rather than the whole token, because a shell bundles short flags:
#: ``set -euo pipefail`` is one token ``-euo`` whose trailing ``o`` takes
#: ``pipefail`` as its argument, exactly as a lone ``-o`` would.
_OPTION_FLAG: Final[str] = "o"
_PIPEFAIL_OPTION: Final[str] = "pipefail"


def _pipefail_toggle(command: Sequence[str]) -> bool | None:
    """Read a ``set`` builtin's effect on ``pipefail``.

    Both directions, not just the disable. Tracking only ``set +o`` makes the
    option a one-way latch: a line that turns it off and back on before its
    pipeline is refused, and refusing a line whose pipeline IS protected
    withholds the evidence a genuine test run produced, which is the failure
    this module's whole conjunctive reading exists to avoid.

    The flag is matched on its shape rather than against ``-o`` and ``+o``
    literally, because ``set -euo pipefail`` is the ordinary way to write this
    line and bundles the option letter into one token. Reading only the exact
    spellings answers "says nothing" for it, so ``set +eo pipefail`` before a
    pipe leaves the option believed ON while the shell has turned it OFF, and
    a pipeline whose exit status is its last command's is then read as
    evidence its first command passed.

    Returns:
        ``True`` when the command enables ``pipefail``, ``False`` when it
        disables it, and ``None`` when it says nothing about it.
    """
    if not command or command[0] != _SET_BUILTIN or _PIPEFAIL_OPTION not in command:
        return None
    # A single command can carry both (``set +o errexit -o pipefail``), so the
    # answer is the flag immediately preceding the option name rather than
    # whichever flag appears anywhere in the line. The index is never zero:
    # reaching here required the first token to be the builtin.
    preceding = command[command.index(_PIPEFAIL_OPTION) - 1]
    if not preceding.endswith(_OPTION_FLAG):
        return None
    if preceding.startswith(_SET_SIGN):
        return True
    if preceding.startswith(_UNSET_SIGN):
        return False
    return None


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

#: Programs whose test runner is selected by a flag rather than a
#: subcommand. ``node --test`` runs Node's built-in runner and exits with
#: the suite's own status, but the flag filter every other shape relies on
#: discards it, so a browser project's suite produced no evidence at all
#: and its deliverable read UNVERIFIED however green the run was.
_FLAG_RUNNERS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {"node": frozenset({"--test"})}
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


def _conjunctive_commands(
    command: str, *, pipefail: bool
) -> tuple[tuple[str, ...], ...] | None:
    """Split *command* into the commands its exit status speaks for.

    Args:
        command: The full command line as it was executed.
        pipefail: Whether the shell running this line has ``pipefail`` set.
            Without it a pipeline's status is its LAST command's, so ``|``
            stops being conjunctive and the line proves nothing about the
            runner to its left.

    Returns:
        The argv of every command in the line when a zero exit status
        proves each of them exited zero, or ``None`` when any part of the
        line breaks that implication.
    """
    if any(char in command for char in _STATEMENT_SEPARATORS):
        return None
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None

    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        if any(char in token for char in _FORBIDDEN_IN_TOKEN):
            return None
        if token in _STATUS_MASKING_TOKENS:
            return None
        if token in _REDIRECTIONS:
            # The descriptor number preceding the operator is part of the
            # redirection, not an argument: ``npm test 2>&1`` lexes as
            # ``npm test 2 >& 1``.
            if current and current[-1].isdigit():
                current.pop()
            skip_target = True
            continue
        if token in _CONJUNCTIVE_SEPARATORS:
            if token == _PIPE and not pipefail:
                return None
            if current:
                # A line may revoke the option the pipe's trustworthiness
                # rests on: after ``set +o pipefail`` a pipeline reports its
                # LAST command's status again, so ``pytest | tail`` exits 0
                # whatever the suite did. Read per segment rather than once
                # up front, because the toggle and the pipeline are separate
                # commands and only a pipe AFTER the toggle is affected.
                toggled = _pipefail_toggle(current)
                if toggled is not None:
                    pipefail = toggled
                segments.append(tuple(current))
            current = []
            continue
        current.append(token)
    if current:
        segments.append(tuple(current))
    return tuple(segments)


def is_test_run(command: str, *, _shell_depth: int = 0, _pipefail: bool = True) -> bool:
    """Whether *command* invokes a recognised test runner.

    Args:
        command: The full command line as it was executed.
        _shell_depth: Recursion guard for a shell's ``-c`` payload, which is
            itself a command line. One level only; a shell invoking a shell
            is not a shape this needs to recognise.
        _pipefail: Whether the shell running this line sets ``pipefail``.
            True at the top level, where every agent line goes through
            :mod:`synthorg.tools._shell_invocation`. False inside a nested
            shell's payload: ``pipefail`` is a shell option, not an
            environment variable, so a fresh shell does not inherit it.

    Returns:
        ``True`` only when a test runner is invoked and the line's exit
        status implies that runner's own.
    """
    segments = _conjunctive_commands(command, pipefail=_pipefail)
    if segments is None:
        return False
    return any(
        _segment_is_test_run(segment, _shell_depth=_shell_depth) for segment in segments
    )


def _segment_is_test_run(parsed: Sequence[str], *, _shell_depth: int) -> bool:
    """Whether one command of a conjunctive line is a test run.

    Args:
        parsed: The command's argv.
        _shell_depth: Recursion guard for a shell's ``-c`` payload.

    Returns:
        ``True`` when the invoked program is a recognised test runner.
    """
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
        # The payload runs in a shell this invocation just started, and
        # ``pipefail`` does not cross that boundary: our own wrapper set it
        # on the OUTER shell only. So a pipeline in here proves nothing,
        # and ``bash -c "npm test | tail -5"`` reports tail's zero.
        return is_test_run(tokens[2], _shell_depth=1, _pipefail=False)
    if program in _DIRECT_RUNNERS:
        return True
    selecting_flags = _FLAG_RUNNERS.get(program)
    if selecting_flags is not None:
        return any(
            token.split("=", 1)[0] in selecting_flags
            for token in tokens[1:]
            if token.startswith("-")
        )
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
