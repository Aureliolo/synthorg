# module-kind: code
"""Whether the deliverable the specification names RUNS, asked apart from the score.

The oracle answers which requirements a tree satisfies. It does not answer
whether the program the specification names is alive, and the two can come
apart: an agent can satisfy a hidden oracle while the requested artefact is
dead (arXiv 2606.28430), and the gap between what visible tests pass and what
held-out ones do grows with the size of the code (arXiv 2605.21384), which is
exactly the direction a depth sweep pushes. The product's own artifact check
asks presence-and-change; nothing asked whether the thing ran.

So each declared module is imported and each declared entry point is executed,
and the verdict is a THIRD state on the cell, reported beside the score and
never folded into it: the two disagreeing is the finding, and a score that
absorbed the verdict would hide it.

Isolation is the oracle's, unchanged. The probe runs the delivered program, so
it runs in the same throwaway container class the oracle uses, on the same
allowlist and with the same environment, and from its OWN scratch root holding
the tree alone: the oracle suite is never staged beside a program this probe
executes, so nothing this probe runs can read an expectation. The working
directory is the staged tree itself, where the program's own root is: a
program that reads a file relative to that root runs there and nowhere else,
and a probe run from beside it would record that program as dead. Interpreter
isolation (``-I``) means that directory has to be put on the path explicitly,
which is done inside the probe code rather than through an environment
variable ``-I`` would ignore.
"""

import asyncio
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from evals.errors import OracleUnusableError
from evals.recursion_depth.grading import (
    GRADED_ENV,
    ORACLE_TREE_DIR,
    SandboxFactory,
    SandboxReleaseHook,
    tail_of,
)
from evals.recursion_depth.models import Liveness
from evals.recursion_depth.oracle import CONTAINER_INTERPRETER, load_index, stage_tree
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_LIVENESS_PROBED
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

#: One interpreter start against the delivered program. A program that cannot
#: import or answer its own entry point inside this is not one that runs.
_LIVENESS_TIMEOUT_SECONDS: Final[float] = 60.0

#: Prefixes the key the probe's container is filed under. Its own rather than
#: the oracle's, so releasing one grading never tears down the other's probe.
_LIVENESS_OWNER_PREFIX: Final[str] = "liveness:"

#: What an uncaught exception looks like on stderr. The one thing that
#: separates a program that ran and exited how it chose (a usage error is
#: exit 2 and no traceback) from one that fell over.
_TRACEBACK_MARKER: Final[str] = "Traceback (most recent call last)"

#: The exit status floor a shell reports for a process a signal ended: 128
#: plus the signal number. A program killed that way (a segfault in an
#: extension, the container's memory limit) writes no traceback, so the
#: marker alone would read it as having exited how it chose.
_SIGNAL_EXIT_FLOOR: Final[int] = 128

#: The key the spec's index declares the probe under.
_LIVENESS_KEY: Final[str] = "liveness"

#: What the probe code puts on ``sys.path``: the working directory, which is
#: the staged tree. ``-I`` implies safe-path, so nothing puts it there
#: otherwise, and a program run as ``python -m`` from its own root would have
#: had it.
_PROBE_PATH_ENTRY: Final[str] = "."


class EntryPoint(BaseModel):
    """One way the specification says the deliverable is run.

    Attributes:
        module: The module ``python -m`` would run.
        args: The arguments it is run with. The verdict does not key on the
            exit status, so these need only be arguments a live program
            answers somehow: a usage error is an answer, a traceback is not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    module: NotBlankStr
    args: tuple[str, ...] = ()

    @field_validator("module")
    @classmethod
    def _module_is_a_dotted_name(cls, value: str) -> str:
        """Refuse a module name the probe code could not spell.

        The name is interpolated into ``-c`` source, so anything that is not
        a dotted identifier would either fail to parse or run as code.

        Returns:
            The validated name.

        Raises:
            ValueError: The name is not a dotted identifier.
        """
        if not all(part.isidentifier() for part in value.split(".")):
            msg = f"{value!r} is not a dotted module name"
            raise ValueError(msg)
        return value


class LivenessDeclaration(BaseModel):
    """What the specification says must run, read off ``requirements.yaml``.

    Attributes:
        modules: Modules that must import from the root of the tree.
        entry_points: Programs that must run without raising.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    modules: tuple[NotBlankStr, ...] = ()
    entry_points: tuple[EntryPoint, ...] = ()

    @field_validator("modules")
    @classmethod
    def _modules_are_dotted_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse a module name the probe code could not spell.

        Returns:
            The validated names.

        Raises:
            ValueError: A name is not a dotted identifier.
        """
        for module in value:
            if not all(part.isidentifier() for part in module.split(".")):
                msg = f"{module!r} is not a dotted module name"
                raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _declares_something(self) -> Self:
        """Refuse a block that names nothing to probe.

        An empty block reads like a declaration and probes like an absent
        one, so every cell would read LIVE on the strength of no question
        having been asked.

        Returns:
            ``self`` when something is declared.

        Raises:
            ValueError: Neither modules nor entry points are named.
        """
        if not self.modules and not self.entry_points:
            msg = "a liveness block must name at least one module or entry point"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class LivenessOutcome:
    """What the probe concluded, and why when it is not LIVE.

    The verdict and its detail agree at construction, where a classifier
    that forgot the reason is cheapest to catch, rather than only once the
    outcome is folded into the cell record.

    Attributes:
        verdict: The verdict.
        detail: What died, or why nothing could be asked. Empty on LIVE and
            on nothing else.
    """

    verdict: Liveness
    detail: str = ""

    def __post_init__(self) -> None:
        """Refuse a live verdict carrying a death, or a death without one.

        Raises:
            ValueError: The verdict and the detail disagree.
        """
        if self.verdict is Liveness.LIVE and self.detail:
            msg = f"a live outcome still says {self.detail!r}"
            raise ValueError(msg)
        if self.verdict is not Liveness.LIVE and not self.detail:
            msg = f"a {self.verdict.value} outcome names no reason"
            raise ValueError(msg)


def declared_liveness(
    index: Mapping[str, object], *, spec_dir: Path
) -> LivenessDeclaration | None:
    """Read the spec's liveness block, or say it declares none.

    Absent is a different fact from malformed: a spec whose deliverable is
    prose declares nothing and every cell reads NOT_PROBEABLE, while a block
    the harness cannot read is refused with the error that stops the matrix,
    since it is true of every cell.

    Args:
        index: The parsed ``requirements.yaml``.
        spec_dir: The specification directory, for the message.

    Returns:
        The declaration, or ``None`` when the spec declares none.

    Raises:
        OracleUnusableError: The block is present and cannot be read.
    """
    if _LIVENESS_KEY not in index:
        return None
    try:
        return LivenessDeclaration.model_validate(index[_LIVENESS_KEY])
    except ValidationError as exc:
        msg = (
            f"{spec_dir}/requirements.yaml declares a liveness block this "
            f"harness cannot read: {exc}"
        )
        raise OracleUnusableError(msg) from exc


def import_argv(module: str) -> tuple[str, ...]:
    """The arguments that import *module* from the working directory, isolated.

    Returns:
        The arguments after the interpreter.
    """
    code = f"import sys; sys.path.insert(0, {_PROBE_PATH_ENTRY!r}); import {module}"
    return ("-I", "-c", code)


def entry_argv(entry: EntryPoint) -> tuple[str, ...]:
    """The arguments that run *entry* as ``python -m`` would, isolated.

    ``runpy`` rather than ``-m``, because ``-I`` implies safe-path and the
    working directory would otherwise not be importable; the path is put
    where the interpreter would have put it, and ``alter_sys`` makes
    ``sys.argv`` and ``__main__`` read as they do under ``-m``.

    Returns:
        The arguments after the interpreter.
    """
    argv = [entry.module, *entry.args]
    code = (
        f"import runpy, sys; sys.path.insert(0, {_PROBE_PATH_ENTRY!r}); "
        f"sys.argv = {argv!r}; "
        f"runpy.run_module({entry.module!r}, run_name='__main__', alter_sys=True)"
    )
    return ("-I", "-c", code)


def classify_import(result: SandboxResult, *, module: str) -> LivenessOutcome:
    """Decide an import probe: anything but a clean exit is dead.

    An import has no exit of its own choosing, so a non-zero status is the
    module failing to load, whether by raising or by exiting the process.

    Returns:
        The outcome.
    """
    if result.timed_out:
        detail = (
            f"importing {module} did not finish inside {_LIVENESS_TIMEOUT_SECONDS}s"
        )
        return LivenessOutcome(Liveness.DEAD, detail)
    if result.returncode != 0:
        detail = f"importing {module} failed: {tail_of(result.stderr)}"
        return LivenessOutcome(Liveness.DEAD, detail)
    return LivenessOutcome(Liveness.LIVE)


def classify_entry(result: SandboxResult, *, entry: EntryPoint) -> LivenessOutcome:
    """Decide an entry-point probe: a traceback, a signal or a hang is dead.

    The exit status is the program's own to choose, and a usage error is an
    answer. What is not an answer is an uncaught exception, which is what a
    traceback on stderr is.

    Returns:
        The outcome.
    """
    what = " ".join([entry.module, *entry.args])
    if result.timed_out:
        detail = f"running {what} did not finish inside {_LIVENESS_TIMEOUT_SECONDS}s"
        return LivenessOutcome(Liveness.DEAD, detail)
    if _TRACEBACK_MARKER in result.stderr:
        detail = f"running {what} raised: {tail_of(result.stderr)}"
        return LivenessOutcome(Liveness.DEAD, detail)
    if result.returncode < 0 or result.returncode >= _SIGNAL_EXIT_FLOOR:
        detail = (
            f"running {what} was ended by a signal (exit {result.returncode}) "
            f"with no traceback: {tail_of(result.stderr)}"
        )
        return LivenessOutcome(Liveness.DEAD, detail)
    return LivenessOutcome(Liveness.LIVE)


async def probe_liveness(
    *,
    build_sandbox: SandboxFactory,
    release_sandboxes: SandboxReleaseHook | None = None,
    spec_dir: Path,
    tree: Path,
    interpreter: str = CONTAINER_INTERPRETER,
) -> LivenessOutcome:
    """Ask whether the deliverable *tree* names runs, in a container of its own.

    Args:
        build_sandbox: Builds the container backend the probe runs in, rooted
            at the scratch directory this assembles.
        release_sandboxes: Reclaims the container this probe opened.
        spec_dir: The specification directory.
        tree: The produced tree.
        interpreter: What runs the probe. Defaults to the bare name, which is
            correct inside the sandbox image.

    Returns:
        The outcome.

    Raises:
        OracleUnusableError: The spec's liveness block cannot be read.
    """
    declaration = declared_liveness(load_index(spec_dir), spec_dir=spec_dir)
    if declaration is None:
        outcome = LivenessOutcome(
            Liveness.NOT_PROBEABLE,
            "the specification declares no module or entry point to probe",
        )
        _log(tree, outcome)
        return outcome
    owner = f"{_LIVENESS_OWNER_PREFIX}{tree}"
    # `mkdtemp` plus an explicit removal, for the reason the oracle does the
    # same: the context manager's teardown is synchronous and walks a copy of
    # the whole tree on the event loop serving the gateway.
    scratch = await asyncio.to_thread(tempfile.mkdtemp)
    root = Path(scratch)
    try:
        try:
            await asyncio.to_thread(stage_tree, root, tree=tree)
            outcome = await _probe(
                root,
                declaration,
                build_sandbox=build_sandbox,
                owner=owner,
                interpreter=interpreter,
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)
    finally:
        if release_sandboxes is not None:
            await release_sandboxes(owner)
    _log(tree, outcome)
    return outcome


async def _probe(
    root: Path,
    declaration: LivenessDeclaration,
    *,
    build_sandbox: SandboxFactory,
    owner: str,
    interpreter: str,
) -> LivenessOutcome:
    """Run every declared probe, stopping at the first that dies.

    Modules first: an entry point of a module that does not import would
    report the same death a second time with a longer traceback.

    Returns:
        LIVE when everything answered, else the first death.
    """
    sandbox = build_sandbox(root, owner=owner)
    # The program's own root, not the scratch root beside it: a delivery that
    # opens a file relative to where it lives runs from there.
    cwd = root / ORACLE_TREE_DIR
    for module in declaration.modules:
        result = await sandbox.execute(
            command=interpreter,
            args=import_argv(module),
            cwd=cwd,
            env_overrides=GRADED_ENV,
            timeout=_LIVENESS_TIMEOUT_SECONDS,
            category=ToolCategory.CODE_EXECUTION.value,
        )
        outcome = classify_import(result, module=module)
        if outcome.verdict is Liveness.DEAD:
            return outcome
    for entry in declaration.entry_points:
        result = await sandbox.execute(
            command=interpreter,
            args=entry_argv(entry),
            cwd=cwd,
            env_overrides=GRADED_ENV,
            timeout=_LIVENESS_TIMEOUT_SECONDS,
            category=ToolCategory.CODE_EXECUTION.value,
        )
        outcome = classify_entry(result, entry=entry)
        if outcome.verdict is Liveness.DEAD:
            return outcome
    return LivenessOutcome(Liveness.LIVE)


def _log(tree: Path, outcome: LivenessOutcome) -> None:
    """Record the verdict where the oracle records its own."""
    logger.info(
        EVALS_RECURSION_LIVENESS_PROBED,
        tree=str(tree),
        verdict=outcome.verdict.value,
        detail=outcome.detail,
    )


__all__ = [
    "EntryPoint",
    "LivenessDeclaration",
    "LivenessOutcome",
    "classify_entry",
    "classify_import",
    "declared_liveness",
    "entry_argv",
    "import_argv",
    "probe_liveness",
]
