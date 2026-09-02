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

Whether a compound line's exit status still implies its runner's own is a
separate question, answered by :mod:`synthorg.core.shell_semantics`: this
module asks what each command IS, and asks it only of the commands that question
has already vouched for.

A test suite is one gate among several. How a project lints, formats and checks
its own dependencies is the project's decision, written into its committed
manifest by the contract stage, so those runs are recognised from the
declaration rather than from a fixed list of programs no such list could hold.
The purpose a run is stamped with is decided here either way, because the
build/test oracle reads the stamp and one module has to own what it means.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import current_execution_identity
from synthorg.core.shell_semantics import (
    conjunctive_commands,
    program_name,
    shell_payload,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    TEST_RUN_RECORD_FAILED,
    TEST_RUN_RECORDED,
)
from synthorg.observability.redaction import scrub_secret_tokens
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)
from synthorg.security.rules.credential_detector import redact_credentials
from synthorg.tools._declared_gate_runs import declared_gate_purposes
from synthorg.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

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


def _strip_wrappers(tokens: Sequence[str]) -> tuple[str, ...]:
    """Drop leading environment assignments and wrapper invocations.

    Returns:
        The remaining tokens, whose head is the program actually being run.
    """
    remaining = tuple(tokens)
    changed = True
    while changed and remaining:
        changed = False
        head = program_name(remaining[0])
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
                program_name(remaining[index]) == part
                for index, part in enumerate(wrapper)
            ):
                remaining = remaining[len(wrapper) :]
                changed = True
                break
    return remaining


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
    segments = conjunctive_commands(command, pipefail=_pipefail)
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
    program = program_name(tokens[0])
    payload = shell_payload(tokens) if _shell_depth == 0 else None
    if payload is not None:
        # The payload runs in a shell this invocation just started, and
        # ``pipefail`` does not cross that boundary: our own wrapper set it
        # on the OUTER shell only. So a pipeline in here proves nothing,
        # and ``bash -c "npm test | tail -5"`` reports tail's zero.
        return is_test_run(payload, _shell_depth=1, _pipefail=False)
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


def _gate_purposes(
    command: str,
    *,
    workspace_root: Path | None,
    project_id: str,
) -> tuple[CodeExecutionPurpose, ...]:
    """Which gates *command* ran.

    The single owner of that decision. The test suite is answered here from the
    command alone; every other gate is the project's own declaration, read by
    :mod:`synthorg.tools._declared_gate_runs`, and the stamping stays here so
    one module decides what a run counts as.

    All of them, because one line can be several: an agent that types
    ``pytest -q && ruff check .`` ran the suite AND the project's lint gate,
    and answering with either alone withholds the evidence for the other.

    Returns:
        Each purpose to record under, empty when the line ran no gate.
    """
    declared = declared_gate_purposes(
        command, workspace_root=workspace_root, project_id=project_id
    )
    if not is_test_run(command):
        return declared
    # The suite is recognised from the invoked program rather than from the
    # manifest, so it can coincide with a declaration only by a project
    # declaring its own test command; deduplicated here so that never doubles
    # the receipt.
    return (
        CodeExecutionPurpose.TESTS,
        *(purpose for purpose in declared if purpose is not CodeExecutionPurpose.TESTS),
    )


def redacted_tail(output: str, *, limit: int) -> str | None:
    """The last *limit* characters of *output*, with credentials masked.

    The tail is persisted and later rendered into the reviewer's prompt, and
    it was printed by whatever the agent ran: a suite that echoes an
    environment, or a build that logs the token it fetched with. The fence
    that block is rendered inside stops an instruction escaping it and does
    nothing about a secret, so the masking happens here, before the row
    exists. Masked BEFORE the cut, so a credential straddling the tail
    boundary is recognised whole rather than surviving as its second half.

    Returns:
        The masked tail, or ``None`` when there was no output.
    """
    if not output:
        return None
    return _masked(output)[-limit:]


def redacted_command(command: str, *, limit: int) -> str:
    """The first *limit* characters of *command*, with credentials masked.

    The command is persisted and rendered beside the output tails as the
    evidence a verdict cites, and an agent types its secrets into it as
    readily as a suite prints them: ``API_TOKEN=... pytest`` is the ordinary
    way to hand a key to a run. Masked BEFORE the cut for the same reason the
    tail is, so a credential straddling the limit is recognised whole.

    Returns:
        The masked command head.
    """
    return _masked(command)[:limit]


def _masked(text: str) -> str:
    """*text* with every credential and secret token replaced.

    Returns:
        The masked text.
    """
    masked, _findings = redact_credentials(text)
    return scrub_secret_tokens(masked)


async def record_if_test_run(
    result: SandboxResult,
    *,
    command: str,
    records: CodeExecutionRecordRepository | None,
    clock: Clock,
    command_repr_limit: int,
    output_tail_limit: int,
    workspace_root: Path | None = None,
) -> None:
    """Persist *result* as gate evidence when *command* ran one.

    A test suite is one gate and the others are the project's own: how it lints,
    formats and checks its dependencies, each declared in its committed manifest
    and each required by the build/test oracle. The suite is recognised from the
    invoked program because the runners are a known set; the rest are recognised
    from the declaration, because they are the project's decision and no list of
    programs could hold them.

    No-ops when the command ran no gate, when no repository is wired, or when
    called outside a bound execution scope. Best-effort: a capture failure logs
    and returns rather than failing the tool call, because losing the receipt
    must not lose the run.

    The record is a MEASUREMENT: ``passed`` says the command exited zero and
    nothing else, which a validator and a database CHECK both hold it to. What
    a failing run MEANS is the build/test oracle's question, and a project that
    declares tests pending answers it differently, because a correct skeleton's
    suite fails by design. That reading lives with the verdict rather than here,
    so a row can never claim a pass the process did not report.

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
        workspace_root: Base directory projects live under, needed to read the
            project's declared gates. ``None`` recognises the test suite alone,
            which is what a caller with no workspace can honestly claim.
    """
    if records is None:
        return
    identity = current_execution_identity()
    if identity is None or identity.project_id is None:
        return
    purposes = _gate_purposes(
        command, workspace_root=workspace_root, project_id=identity.project_id
    )
    if not purposes:
        return
    executed_at = clock.now()
    persisted_command = redacted_command(command, limit=command_repr_limit)
    try:
        for purpose in purposes:
            # One row per gate the line satisfied, each carrying the whole
            # command: the row says which gate this run is evidence for, and a
            # compound line is evidence for each of them. They share one
            # timestamp because they share one execution.
            await records.append(
                CodeExecutionRecord(
                    task_id=identity.task_id,
                    execution_id=identity.execution_id,
                    project_id=identity.project_id,
                    purpose=purpose,
                    command=persisted_command,
                    returncode=result.returncode,
                    passed=result.success,
                    timed_out=result.timed_out,
                    stdout_tail=redacted_tail(result.stdout, limit=output_tail_limit),
                    stderr_tail=redacted_tail(result.stderr, limit=output_tail_limit),
                    executed_at=executed_at,
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
