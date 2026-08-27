# module-kind: code
"""Run a delivered tree, in a container, and read its verdict off a report.

A delivered tree is model output. Grading it means running it, and running it
means importing every ``conftest.py`` and package ``__init__.py`` the agent
wrote, so the process that grades is a process the agent authored. The harness
already says this about the agent's own shell commands
(:mod:`evals.harness.binding` puts ``terminal`` in the untrusted categories and
runs it at ``network=none``); grading the ARTEFACTS of those commands on the
host restored, for exactly the same code, the network, the operator's
credentials and the Docker socket that grants host root.

So both graded runs go into the sandbox image the CLI verified, with no network
and only the environment this module hands them.

What that does and does not buy is worth stating plainly, because the two are
easy to conflate.

It buys CONTAINMENT, completely: whatever the tree does, it does inside a
throwaway container with no network and no credentials, and the host is not
reachable from it.

It does NOT buy an unforgeable GRADE, and nothing can. A tree that wants to
report that its tests passed is running arbitrary code in the process doing the
reporting; it can write the report file itself. What the report buys over an
exit code is that the ORDINARY failures stop reading as passes: ``os._exit(0)``
in a ``conftest.py``, a suite that collected nothing, a
``pytest_collection_modifyitems`` that deselected everything. Those are what a
model actually does under pressure, and each of them now fails rather than
grading clean.

A deliberate forgery of the unit's OWN suite is still possible, and it is
bounded: a forged pass adds that unit's claims to the survival DENOMINATOR and
nothing to the numerator, so forging drives the measured result down rather than
up. There is no incentive gradient toward it and no way to profit from it.

The numerator is a different question, and the containerisation created it
before closing it. The held-out oracle has to read its assertions and the
delivered program has to be executable, and in a container there is one
filesystem, so the two are staged as siblings and one ``..`` from the program's
working directory would reach the expected outputs. That is why the oracle
suite deletes its own expectations once collection has imported them, before any
test body runs and therefore before the delivered program is ever spawned: see
``spec/sqlcsv/oracle/conftest.py``.

What may remain is an ALLOWLIST (:data:`ORACLE_KEEP_FILES`,
:data:`ORACLE_KEEP_DIRS`) rather than a set of patterns to remove, because the
version that swept ``test_*.py`` left ``__pycache__`` in place and the compiled
modules carried the same queries and expected rows in ``co_consts``. The
adjacency is therefore not prevented by construction, it is enforced: nothing
compiled is staged, the sweep removes anything outside the allowlist, the suite
re-checks before every spawn and the harness re-checks after the run and refuses
the measurement outright.
"""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable
from xml.etree import ElementTree as ET

from evals.errors import EvalToolMissingError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_GRADED
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

#: Where a graded run writes its machine-readable verdict, relative to the tree.
#: Dot-prefixed so it is not importable and cannot be mistaken for a deliverable.
REPORT_NAME: Final[str] = ".synthorg-grade.xml"

#: The configuration the graded run is pointed at. The tree is mounted alone so
#: nothing walks up to the repository's own ``pyproject.toml``, but a tree
#: carrying its own ``pytest.ini`` would otherwise become rootdir and choose its
#: own ``pythonpath`` and ``python_files``.
INI_NAME: Final[str] = ".synthorg-grade.ini"

#: How long one graded suite may run inside the container.
OWN_TESTS_TIMEOUT_SECONDS: Final[float] = 600.0

INI_BODY: Final[str] = "[pytest]\naddopts =\n"

#: What the graded interpreter is told, and nothing else. The sandbox passes
#: only these (its own contract: "no host leakage"), which is what keeps the
#: operator's provider keys and the bootstrap secrets out of agent-authored
#: code. ``PYTHONPATH`` is set EMPTY rather than omitted because the recorder is
#: launched with ``PYTHONPATH=.`` (``evals`` is out-of-package), a relative
#: entry is absolutised against the CHILD's working directory, and that
#: directory is the tree: inherited, it would put a ``sitecustomize.py`` the
#: agent wrote on the import path, where it runs before any interpreter flag is
#: parsed. ``PYTHONNOUSERSITE`` closes the same door through the user site.
#:
#: ``PYTHONDONTWRITEBYTECODE`` is confinement, not tidiness. pytest's assertion
#: rewriter gates its own cache write on ``sys.dont_write_bytecode``, so without
#: this the oracle's expectations would be recompiled beside the graded tree
#: DURING the run, after the sweep that removed them and before the program that
#: must not read them is spawned.
GRADED_ENV: Final[dict[str, str]] = {
    "PYTHONPATH": "",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}


#: Where the graded tree is placed inside the oracle's throwaway container.
ORACLE_TREE_DIR: Final[str] = "tree"

#: Where the held-out oracle is placed beside it. Both live in a scratch
#: directory built per grading and destroyed after, which no agent workspace is
#: ever mounted from: the oracle stays invisible because it only exists
#: somewhere no agent runs, rather than because nothing happened to copy it.
ORACLE_SUITE_DIR: Final[str] = "oracle"

#: Everything the staged oracle may still hold once its expectations are gone,
#: as an ALLOWLIST rather than a list of things to remove.
#:
#: A denylist is one file extension away from being wrong, and was: the first
#: version of this swept ``test_*.py`` and left ``__pycache__`` behind, so the
#: compiled expectations sat beside the graded tree with the queries and their
#: expected rows readable out of ``co_consts``. Nobody had thought of the
#: extension; an allowlist does not require anybody to.
#:
#: ``conftest.py`` and ``__init__.py`` stay because pytest re-reads both while
#: setting a test up and the run dies without them, and neither holds an
#: expected output: the conftest is invocation machinery the CLI already learns
#: from its own argv. ``data/`` stays because it is the input a query runs
#: against, and turning that into the right answer is the work being graded.
ORACLE_KEEP_FILES: Final[frozenset[str]] = frozenset({"conftest.py", "__init__.py"})

#: Directories under the staged oracle that survive whole.
ORACLE_KEEP_DIRS: Final[frozenset[str]] = frozenset({"data"})


def is_kept(relative: Path) -> bool:
    """Whether the allowlist admits *relative*.

    The one predicate both the leftover sweep and the fingerprint read, so the
    set they disagree about cannot exist: one asks what must be GONE and the
    other asks whether what REMAINS is unchanged, and those are complements.

    Args:
        relative: A path relative to the staged oracle directory.

    Returns:
        Whether it may still be there once the expectations are swept.
    """
    if relative.parts[0] in ORACLE_KEEP_DIRS:
        return True
    return len(relative.parts) == 1 and relative.name in ORACLE_KEEP_FILES


def oracle_fingerprint(suite_dir: Path) -> str:
    """Digest what the staged oracle is allowed to KEEP, and only that.

    The retained ``data/`` fixtures are the INPUT every query is run against,
    and the delivered program runs in the same mount with the same identity as
    pytest, so it can rewrite them between one test and the next. Nothing else
    would notice: :func:`oracle_leftovers` reads names, and a delivery that
    edited a fixture to match what it happens to produce would be scored
    against its own answer.

    Restricted to the allowlisted set rather than the whole directory, because
    the run is SUPPOSED to change the rest: the sweep unlinks the expectations
    partway through, so a digest over everything differs from itself on every
    honest run.

    Taken by the harness before the container starts and re-taken after it
    exits, which is outside anything the tree can reach.

    Args:
        suite_dir: The staged oracle directory.

    Returns:
        A hex digest over every retained file's path and bytes.
    """
    digest = hashlib.sha256()
    for path in sorted(suite_dir.rglob("*")):
        if path.is_dir() or path.is_symlink():
            continue
        relative = path.relative_to(suite_dir)
        if not is_kept(relative):
            continue
        digest.update(str(relative).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def oracle_leftovers(suite_dir: Path) -> tuple[Path, ...]:
    """Every staged file the oracle should not still be holding.

    The allowlist above decides, so a file type nobody anticipated is refused
    by default rather than admitted by omission.

    Args:
        suite_dir: The staged oracle directory.

    Returns:
        The offending paths, relative to *suite_dir*, empty when clean.
    """
    offenders: list[Path] = []
    for path in sorted(suite_dir.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(suite_dir)
        if is_kept(relative):
            continue
        offenders.append(relative)
    return tuple(offenders)


@runtime_checkable
class UnitGrader(Protocol):
    """Whatever decides, from a tree, whether the tests in it pass."""

    async def own_tests_pass(self, project_dir: Path) -> tuple[bool, str]:
        """Run the tests a unit wrote for itself and report the verdict."""
        ...


@runtime_checkable
class SandboxFactory(Protocol):
    """Builds a container backend rooted at a directory on the host."""

    def __call__(self, root: Path, /, *, owner: str) -> SandboxBackend:
        """Return a sandbox whose workspace is *root*, tracked under *owner*.

        The owner is what may later release it, and nothing else may. Sandboxes
        are opened by concurrent units, and a teardown latches: one taken from
        a unit still running does not reopen for it.
        """
        ...


#: Reclaims every container filed under one owner. The counterpart to
#: :class:`SandboxFactory`: whoever names an owner when it opens a sandbox is
#: what names it again to close one.
SandboxReleaseHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class SandboxUnitGrader:
    """Grades a unit's own suite inside the verified sandbox image.

    Attributes:
        sandbox: The container backend the suite runs in, scoped to the cell
            workspace the graded tree sits under.
        project_id: The project whose mount the sandbox resolves.
    """

    sandbox: SandboxBackend
    project_id: NotBlankStr

    async def own_tests_pass(self, project_dir: Path) -> tuple[bool, str]:
        """Run the unit's own suite in a container and read its report.

        Args:
            project_dir: The unit's produced tree, on the host.

        Returns:
            Whether the suite ran clean, and a short report when it did not.
        """
        # BEFORE the tree runs, so the systemic answer is evidence the tree
        # cannot have touched.
        refuse_without_a_runner(
            await self.sandbox.execute(
                command="python",
                args=RUNNER_PROBE_ARGS,
                cwd=project_dir,
                env_overrides=GRADED_ENV,
                timeout=OWN_TESTS_TIMEOUT_SECONDS,
                category=ToolCategory.CODE_EXECUTION.value,
                owner_id=NotBlankStr(str(project_dir)),
                project_id=self.project_id,
            )
        )
        (project_dir / INI_NAME).write_text(INI_BODY, encoding="utf-8")
        report_path = project_dir / REPORT_NAME
        report_path.unlink(missing_ok=True)
        result = await self.sandbox.execute(
            command="python",
            args=(
                "-m",
                "pytest",
                "-c",
                INI_NAME,
                "-p",
                "no:cacheprovider",
                "-q",
                f"--junit-xml={REPORT_NAME}",
                ".",
            ),
            cwd=project_dir,
            env_overrides=GRADED_ENV,
            timeout=OWN_TESTS_TIMEOUT_SECONDS,
            category=ToolCategory.CODE_EXECUTION.value,
            # Keyed on the tree, so a reusing lifecycle can never hand one
            # unit's graded run the container another unit's ran in. Today the
            # wiring builds a sandbox per grading and the separation holds
            # without this; that is a property of the wiring, and hoisting the
            # factory out for the obvious reason (why build a container per
            # unit?) would silently let unit N read whatever unit N-1 left
            # outside the mount. Stated here so the isolation belongs to the
            # grader rather than to how it happens to be constructed.
            owner_id=NotBlankStr(str(project_dir)),
            project_id=self.project_id,
        )
        passed, detail = read_verdict(report_path, timed_out=result.timed_out)
        logger.info(
            EVALS_RECURSION_GRADED,
            tree=str(project_dir),
            passed=passed,
            returncode=result.returncode,
            timed_out=result.timed_out,
        )
        if passed:
            return True, ""
        return False, detail or tail_of(result.stdout + result.stderr)


#: What a PROBE runs to establish that the interpreter can run a suite at all.
#:
#: ``-I`` is the whole point. Isolated mode drops the working directory from
#: ``sys.path``, ignores the environment and ignores the user site, so the
#: answer describes the interpreter and nothing else. Without it the probe runs
#: with the graded tree's own directory first on the path, where an
#: agent-authored ``pytest.py`` decides the result.
RUNNER_PROBE_ARGS: Final[tuple[str, ...]] = ("-I", "-c", "import pytest")


def refuse_without_a_runner(probe: SandboxResult) -> None:
    """Refuse to grade at all when the interpreter cannot run a suite.

    An interpreter without pytest fails identically to a delivery that wrote no
    report, so the sweep would record every unit as undelivered and publish an
    empty survival curve. That is the worst failure available here: it does not
    look like a broken harness, it looks like a catastrophic but legitimate
    result, which is the shape of the finding the experiment exists to measure.

    It is asked of a PROBE run before any agent-authored code, never inferred
    from the graded run's own output. The output route was tried twice and both
    versions were reachable by the tree: the marker alone lets a delivered test
    print the message and abort a matrix mid-sweep, and corroborating it with a
    missing report only widens the recipe, since the tree can delete the report
    too. Since the systemic decision stops the whole run, its evidence has to
    come from somewhere the run cannot reach, and "before it started" is the
    only such place.

    ANY non-zero status refuses, rather than a message match. The question is
    whether this interpreter can import pytest, and every way of answering no
    is the same answer.

    A missing tool is systemic rather than per cell, so this raises the error
    the matrix stops on rather than failing one unit.

    Args:
        probe: What :data:`RUNNER_PROBE_ARGS` returned.

    Raises:
        EvalToolMissingError: The interpreter cannot import pytest.
    """
    if probe.returncode == 0:
        return
    msg = (
        "the graded interpreter cannot import pytest, so nothing can be graded "
        "and every unit would read as undelivered. In a recording that is the "
        "sandbox image, built from docker/sandbox/apko.yaml which declares "
        "py3.14-pytest; a published tag from before that was added does not "
        "carry it, so build the image from this tree and pass --sandbox-image. "
        "Under a host-side sandbox it is the interpreter PATH resolved, which "
        "is the system one rather than this project's environment"
    )
    raise EvalToolMissingError(msg)


def read_verdict(report_path: Path, *, timed_out: bool) -> tuple[bool, str]:
    """Decide the verdict from the report pytest wrote, not from an exit code.

    A run that produced no report did not pass, whatever it exited with: the
    report is written at session end, so its absence means the session never
    got there. A report describing zero tests did not pass either, because a
    suite that collected nothing has demonstrated nothing.

    Args:
        report_path: Where the graded run was told to write its report.
        timed_out: Whether the container killed the run first.

    Returns:
        Whether the suite ran clean, and why not when it did not.
    """
    if timed_out:
        return False, f"the suite did not finish in {OWN_TESTS_TIMEOUT_SECONDS:.0f}s"
    if not report_path.is_file():
        return False, "the suite wrote no report, so it never reached session end"
    try:
        root = ET.parse(report_path).getroot()  # noqa: S314 -- the harness's own path
    except ET.ParseError:
        return False, "the suite's report was not readable"
    return _verdict_from(_totals(root))


def _totals(root: ET.Element) -> dict[str, int]:
    """Sum the junit counts across every suite in the report.

    Returns:
        The summed ``tests``, ``failures`` and ``errors``.
    """
    suites = [root] if root.tag == "testsuite" else [root, *root.iter("testsuite")]
    totals: dict[str, int] = dict.fromkeys(("tests", "failures", "errors"), 0)
    for suite in suites:
        for key in totals:
            totals[key] += _count(suite.get(key))
    return totals


def _verdict_from(totals: dict[str, int]) -> tuple[bool, str]:
    """Read a pass or a reason off the summed counts.

    Returns:
        Whether the suite ran clean, and why not when it did not.
    """
    if totals["tests"] == 0:
        return False, "the suite collected no tests"
    if totals["failures"] or totals["errors"]:
        detail = (
            f"{totals['failures']} failed and {totals['errors']} errored "
            f"of {totals['tests']}"
        )
        return False, detail
    return True, ""


def _count(raw: str | None) -> int:
    """Read one junit count attribute.

    Returns:
        The parsed count, ``0`` when absent or not a number.
    """
    if raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


#: How much of a failing run's output travels with the record. Bounded because
#: this is agent-authored text on its way into a committed artifact.
_TAIL_CHARS: Final[int] = 800


def tail_of(output: str) -> str:
    """Bound and flatten agent-authored output for the record.

    Control characters are stripped rather than escaped: this ends up in a
    committed JSON artifact and in a terminal, and an escape sequence from a
    delivered tree has no business reaching either.

    Args:
        output: The captured output.

    Returns:
        The trailing extract, printable characters only.
    """
    printable = "".join(char for char in output if char.isprintable() or char == "\n")
    return printable[-_TAIL_CHARS:].strip()


__all__ = [
    "GRADED_ENV",
    "INI_BODY",
    "INI_NAME",
    "ORACLE_KEEP_DIRS",
    "ORACLE_KEEP_FILES",
    "ORACLE_SUITE_DIR",
    "ORACLE_TREE_DIR",
    "OWN_TESTS_TIMEOUT_SECONDS",
    "REPORT_NAME",
    "RUNNER_PROBE_ARGS",
    "SandboxFactory",
    "SandboxReleaseHook",
    "SandboxUnitGrader",
    "UnitGrader",
    "is_kept",
    "oracle_fingerprint",
    "oracle_leftovers",
    "read_verdict",
    "refuse_without_a_runner",
    "tail_of",
]
