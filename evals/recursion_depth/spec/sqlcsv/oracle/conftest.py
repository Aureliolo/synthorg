# module-kind: tests
"""How the held-out oracle reaches the tree under test.

This suite never imports the delivered code. It runs ``python -m sqlcsv`` as a
subprocess against the tree the run produced and reads stdout, stderr and the
exit code, so it can only observe behaviour the spec actually describes. A test
that reached inside would be checking an implementation the spec left open, and
every decomposition invents a different one.

The tree arrives through ``--tree``. Nothing here is copied into a workspace: an
agent that could read these files would build to them rather than to the
requirement, which is the failure mode that made an exposed 222-test oracle
score near-perfect over a library that was dead outside the tested paths.

Grading happens in a container, which puts this suite and the tree in one mount
by necessity: pytest has to read the assertions and the delivered program has to
be executable, and there is only one filesystem. Adjacency would hand the tree
exactly what it must not have, since ``_run`` spawns it with ``cwd`` set to a
sibling of this directory and one ``..`` would reach the expected outputs.

So the assertions delete themselves. Every test module here, and this file, are
unlinked once collection has imported them, before any test body runs and
therefore before the delivered program is ever executed. What stays is ``data/``,
which the CLI needs as input and which reveals nothing: the fixtures are what a
query runs against, and turning them into the right answer is the work being
graded. ``_run`` re-checks the deletion before every spawn rather than trusting
it, and the harness refuses the whole measurement if any module survived, so
this is enforced at two levels rather than assumed at none.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest

#: Long enough for an interpreter start plus a query over a fixture of tens of
#: rows, short enough that a deadlocked delivery fails the requirement rather
#: than the run.
_RUN_TIMEOUT_SECONDS = 60

#: The fixtures every query here runs against, beside this file so the data and
#: the assertions about it cannot drift apart.
DATA_DIR = Path(__file__).parent / "data"

#: The data directory a query uses unless it says otherwise.
_DEFAULT_DATA = "shop"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the tree the oracle grades and where its verdicts are written."""
    parser.addoption(
        "--tree",
        action="store",
        required=True,
        help="Root of the produced tree that should hold an importable sqlcsv.",
    )
    parser.addoption(
        "--report-json",
        action="store",
        default=None,
        help="Write a {requirement-node: passed} map here.",
    )


#: This suite's own directory, which stops existing as Python source partway
#: through the session. Captured at import because ``__file__`` is about to be
#: unlinked.
_SUITE_DIR = Path(__file__).parent

#: Per-node phase outcomes, accumulated across setup, call and teardown so a
#: test that errored in a fixture is recorded as failed rather than as absent.
_OUTCOMES: dict[str, bool] = {}


def surviving_expectations() -> tuple[Path, ...]:
    """Every test module still readable under this suite.

    The test modules are where the expected outputs live, and they are the only
    thing here a delivery must not read: this file describes how the CLI is
    invoked, which the CLI learns from its own argv anyway, and ``data/`` is the
    input a query runs against, which is the work rather than the answer.

    Read by the harness after the run as well as by ``_run`` before each spawn,
    because "the tree cannot read its own oracle" is the sentence the whole
    measurement rests on and an unenforced claim is how it stops being true.

    Returns:
        The surviving test modules, empty once collection has cleaned up.
    """
    return tuple(sorted(_SUITE_DIR.rglob("test_*.py")))


def pytest_collection_finish(session: pytest.Session) -> None:
    """Unlink this suite's expectations once every module has been imported.

    Collection is where pytest reads and imports the test modules, so by the
    time this fires the assertions are objects in memory and the files are dead
    weight. Test BODIES run after it, and a body is the only thing that spawns
    the delivered program, so the program never coexists with the expectations
    it is judged against.

    Only ``test_*.py`` goes. ``__init__.py`` stays because pytest re-reads it
    when setting a test up and the run dies without it, and it is empty;
    ``conftest.py`` stays for the same reason and because it holds machinery
    rather than answers.

    The cost is that a failure's traceback can no longer quote its own source.
    That is worth paying: the harness reads pass or fail per node and nothing
    else, and the alternative is handing a tree the answers.
    """
    del session
    for module in surviving_expectations():
        module.unlink(missing_ok=True)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Record whether one node has failed any phase.

    A requirement is satisfied only if every phase of its test came through, so
    the verdict starts true at setup and is turned off by the first failure
    rather than being read off the call phase alone.
    """
    key = _node_key(report.nodeid)
    if report.failed:
        _OUTCOMES[key] = False
    elif report.when == "call" and report.passed:
        _OUTCOMES.setdefault(key, True)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Write the per-node verdicts where the caller asked for them."""
    destination = session.config.getoption("--report-json")
    if destination is None:
        return
    Path(str(destination)).write_text(json.dumps(_OUTCOMES), encoding="utf-8")


def _node_key(node_id: str) -> str:
    """Reduce a pytest node id to the form the requirement index uses.

    The index names ``test_file.py::test_name``, which is stable wherever the
    suite is invoked from; a raw node id is relative to whatever rootdir pytest
    resolved and would stop matching the moment the harness ran from elsewhere.

    Returns:
        The ``<file>::<test>`` key.
    """
    path, _, rest = node_id.partition("::")
    return f"{Path(path).name}::{rest}" if rest else Path(path).name


@dataclass(frozen=True)
class CliResult:
    """One invocation's observable behaviour.

    Attributes:
        exit_code: The process exit status.
        stdout: Decoded stdout with newlines normalised.
        stderr: Decoded stderr, likewise.
    """

    exit_code: int
    stdout: str
    stderr: str

    @property
    def lines(self) -> list[str]:
        """Stdout split into lines, without a trailing empty one.

        Returns:
            The lines of stdout.
        """
        return self.stdout.removesuffix("\n").split("\n") if self.stdout else []


class SqlRunner(Protocol):
    """Runs one statement against the produced tree."""

    def __call__(
        self,
        sql: str,
        *,
        data: str = _DEFAULT_DATA,
        fmt: str | None = None,
        stdin: str | None = None,
        extra: tuple[str, ...] = (),
    ) -> CliResult:
        """Invoke the CLI and return what it did.

        Returns:
            The invocation's result.
        """
        ...


class JsonRunner(Protocol):
    """Runs one statement and decodes its JSON rows, asserting success."""

    def __call__(
        self, sql: str, *, data: str = _DEFAULT_DATA
    ) -> list[dict[str, object]]:
        """Invoke the CLI in JSON mode and decode the rows.

        Returns:
            The decoded row objects.
        """
        ...


@pytest.fixture
def tree(request: pytest.FixtureRequest) -> Path:
    """The produced tree the oracle grades.

    Returns:
        The tree root.
    """
    return Path(str(request.config.getoption("--tree"))).resolve()


@pytest.fixture
def run_sql(tree: Path) -> SqlRunner:
    """Return a helper that runs one query against the produced tree.

    Returns:
        The runner.
    """

    def _run(
        sql: str,
        *,
        data: str = _DEFAULT_DATA,
        fmt: str | None = None,
        stdin: str | None = None,
        extra: tuple[str, ...] = (),
    ) -> CliResult:
        # Checked before every spawn, not once at session start: this is the
        # moment the delivered program gets a process, and if the assertions
        # are still on disk it gets them too. Cheap enough to pay per call, and
        # the only place that can fail CLOSED rather than after the fact.
        surviving = surviving_expectations()
        if surviving:
            msg = (
                f"the oracle's expectations are still readable at "
                f"{[path.name for path in surviving]}, so the tree about to be "
                f"run can read what it is graded against"
            )
            raise RuntimeError(msg)
        argv = [sys.executable, "-m", "sqlcsv"]
        if data:
            argv += ["--data", str(DATA_DIR / data)]
        if fmt is not None:
            argv += ["--format", fmt]
        argv += [*extra, sql]
        completed = subprocess.run(  # noqa: S603 -- interpreter path, fixed argv
            argv,
            cwd=tree,
            capture_output=True,
            # Bytes, not text: on Windows a text-mode stdin rewrites every
            # newline it sends, so a statement passed through the pipe arrives
            # carrying carriage returns the argv form never had, and the same
            # requirement then passes one way and fails the other.
            input=None if stdin is None else stdin.encode("utf-8"),
            timeout=_RUN_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
        return CliResult(
            exit_code=completed.returncode,
            stdout=_decode(completed.stdout),
            stderr=_decode(completed.stderr),
        )

    return _run


@pytest.fixture
def json_rows(run_sql: SqlRunner) -> JsonRunner:
    """Return a helper that runs a query and decodes its JSON rows.

    Most requirements are about which rows come back rather than about how they
    are rendered, so they are asserted through the one rendering with no layout
    of its own.

    Returns:
        The runner.
    """

    def _rows(sql: str, *, data: str = _DEFAULT_DATA) -> list[dict[str, object]]:
        result = run_sql(sql, data=data, fmt="json")
        assert result.exit_code == 0, result.stderr
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, list), f"expected a JSON array, got {parsed!r}"
        return parsed

    return _rows


def _decode(raw: bytes) -> str:
    """Decode a captured stream and normalise its line endings.

    Returns:
        The decoded text.
    """
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
