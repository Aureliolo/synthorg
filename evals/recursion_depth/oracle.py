# module-kind: code
"""Run the held-out oracle against a produced tree.

The oracle lives outside every workspace and is named in no brief. Grading a
tree means importing whatever the agent wrote into it, so the run goes into a
container, on the sandbox image the CLI verified, with no network and only the
environment :mod:`evals.recursion_depth.grading` hands it. Run on the host it
would have given a delivered ``conftest.py`` the operator's credentials and the
Docker socket, which is host root.

The container is built per grading from a scratch directory holding a copy of
the tree beside a copy of the oracle, and destroyed after. That is what keeps
the oracle held out: it only ever exists somewhere no agent runs, rather than
being kept away from agents by nothing having copied it.

The invocation is pointed at a configuration this module writes, rather than
merely clearing ``addopts``. Clearing ``addopts`` leaves ``timeout``,
``filterwarnings`` and ``pythonpath`` inherited from whatever ini file pytest
resolves as rootdir, and two of those change the verdict: ``filterwarnings =
["error", ...]`` fails a CORRECT delivery over a stray ``ResourceWarning``, and
a 30-second per-test timeout undercuts this oracle's own per-invocation ceiling.
"""

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from evals.errors import OracleUnusableError
from evals.recursion_depth.grading import (
    GRADED_ENV,
    INI_BODY,
    INI_NAME,
    ORACLE_SUITE_DIR,
    ORACLE_TREE_DIR,
    SandboxFactory,
    drop_escaping_links,
    oracle_leftovers,
    tail_of,
)
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_ORACLE_RUN
from synthorg.security.autonomy.enums import ToolCategory

logger = get_logger(__name__)

#: Long enough for dozens of interpreter starts against a slow tree, short
#: enough that a delivery which deadlocks fails the run rather than holding it.
_ORACLE_TIMEOUT_SECONDS: Final[float] = 900.0

#: Where the oracle writes its per-node verdicts, inside the scratch root that
#: is mounted into the grading container.
_REPORT_NAME: Final[str] = "report.json"

#: The pytest exit status meaning every collected test passed.
_PYTEST_OK: Final[int] = 0

#: The pytest exit status meaning tests ran and some failed. Anything else is a
#: fault of the invocation rather than a verdict on the tree.
_PYTEST_TESTS_FAILED: Final[int] = 1

#: What is never copied into the staged oracle in the first place. The deletion
#: and the refusal are both allowlists and would catch these anyway; not staging
#: them means the window between staging and collection does not exist either.
_NEVER_STAGED: Final[tuple[str, ...]] = ("__pycache__", "*.pyc", "*.pyo")


@dataclass(frozen=True)
class OracleOutcome:
    """What the oracle made of one tree.

    Attributes:
        results: Every declared requirement id mapped to whether it passed.
        report: The captured pytest output, for a human reading a failure.
    """

    results: dict[str, bool]
    report: str

    @property
    def passed(self) -> frozenset[str]:
        """The requirements this tree satisfies.

        Returns:
            The passing requirement ids.
        """
        return frozenset(key for key, ok in self.results.items() if ok)

    @property
    def failed(self) -> frozenset[str]:
        """The requirements this tree does not satisfy.

        Returns:
            The failing requirement ids.
        """
        return frozenset(key for key, ok in self.results.items() if not ok)


def load_index(spec_dir: Path) -> dict[str, object]:
    """Read a spec's requirement index.

    Args:
        spec_dir: The specification directory.

    Returns:
        The parsed ``requirements.yaml``.
    """
    text = (spec_dir / "requirements.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        msg = f"{spec_dir / 'requirements.yaml'} does not parse to a mapping"
        raise OracleUnusableError(msg)
    return parsed


def requirement_ids(spec_dir: Path) -> tuple[str, ...]:
    """Every requirement id the spec declares, in declaration order.

    Args:
        spec_dir: The specification directory.

    Returns:
        The requirement ids.
    """
    index = load_index(spec_dir)
    entries = index["requirements"]
    if not isinstance(entries, list):
        msg = "requirements.yaml declares no requirement list"
        raise OracleUnusableError(msg)
    return tuple(str(entry["id"]) for entry in entries)


def node_ids(spec_dir: Path) -> dict[str, str]:
    """Map each requirement id to the oracle node that decides it.

    Args:
        spec_dir: The specification directory.

    Returns:
        The requirement-to-node map.
    """
    index = load_index(spec_dir)
    entries = index["requirements"]
    if not isinstance(entries, list):
        msg = "requirements.yaml declares no requirement list"
        raise OracleUnusableError(msg)
    return {str(entry["id"]): str(entry["oracle"]) for entry in entries}


async def run_oracle(
    *,
    build_sandbox: SandboxFactory,
    spec_dir: Path,
    tree: Path,
    only: frozenset[str] | None = None,
) -> OracleOutcome:
    """Grade *tree* against the spec's held-out oracle, in a container.

    Args:
        build_sandbox: Builds the container backend the grading runs in, rooted
            at the scratch directory this assembles.
        spec_dir: The specification directory.
        tree: The produced tree to grade.
        only: Restrict the run to these requirement ids. ``None`` runs all of
            them, which is what the final merged tree is graded by.

    Returns:
        The outcome, with one verdict per requirement asked about.

    Raises:
        OracleUnusableError: pytest could not run the oracle at all, so there
            is no verdict to record.
    """
    nodes = node_ids(spec_dir)
    wanted = tuple(key for key in nodes if only is None or key in only)
    if not wanted:
        return OracleOutcome(results={}, report="")
    oracle_dir = spec_dir / str(load_index(spec_dir)["oracle_dir"])
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        await asyncio.to_thread(stage, root, tree=tree, oracle_dir=oracle_dir)
        report_path = root / _REPORT_NAME
        result = await build_sandbox(root).execute(
            command="python",
            args=oracle_argv(nodes=nodes, wanted=wanted),
            cwd=root,
            env_overrides=GRADED_ENV,
            timeout=_ORACLE_TIMEOUT_SECONDS,
            category=ToolCategory.CODE_EXECUTION.value,
        )
        report = tail_of(result.stdout + result.stderr)
        if result.timed_out:
            msg = (
                f"the oracle did not finish inside {_ORACLE_TIMEOUT_SECONDS}s "
                f"against {tree}; a delivery that deadlocks is a failed "
                f"requirement, but a run that cannot report which one is not a "
                f"measurement"
            )
            raise OracleUnusableError(msg)
        if result.returncode not in (_PYTEST_OK, _PYTEST_TESTS_FAILED):
            msg = (
                f"the oracle could not be run against {tree} "
                f"(pytest exited {result.returncode}):\n{report}"
            )
            raise OracleUnusableError(msg)
        refuse_if_oracle_survived(root)
        results = _read_report(
            report_path, nodes=nodes, wanted=wanted, returncode=result.returncode
        )
    logger.info(
        EVALS_RECURSION_ORACLE_RUN,
        tree=str(tree),
        requested=len(wanted),
        passed=sum(1 for ok in results.values() if ok),
    )
    return OracleOutcome(results=results, report=report)


def refuse_if_oracle_survived(root: Path) -> None:
    """Refuse a measurement taken while the tree could read its expectations.

    The suite unlinks its expectations once collection has imported them, so the
    delivered program never runs beside the assertions it is judged against.
    Checked here as well because that deletion happens inside code the graded
    tree shares a filesystem with, and an oracle that quietly stopped deleting
    itself would keep producing verdicts that looked exactly like honest ones.

    What counts as an expectation is decided by
    :func:`evals.recursion_depth.grading.oracle_leftovers`, which names what may
    REMAIN rather than what must go. Asking the question the other way round is
    how the compiled modules survived a check written to catch exactly them.

    Args:
        root: The scratch directory the grading ran in.

    Raises:
        OracleUnusableError: Something outlived collection that should not have.
    """
    survivors = oracle_leftovers(root / ORACLE_SUITE_DIR)
    if not survivors:
        return
    names = [str(Path(ORACLE_SUITE_DIR) / path) for path in survivors]
    msg = (
        f"the oracle's source outlived its own collection ({names}), so the "
        f"graded tree ran beside the expectations it is judged against and this "
        f"measurement cannot be trusted"
    )
    raise OracleUnusableError(msg)


def stage(root: Path, *, tree: Path, oracle_dir: Path) -> None:
    """Lay the graded tree and the oracle out side by side for the container.

    The tree is copied WITHOUT following symlinks, for the reason
    :func:`evals.recursion_depth.merge.mount_children` does not follow them: a
    link in an agent-authored tree names a host path the agent chose, and
    resolving it here would pull the repository, this oracle included, into the
    directory about to be mounted.

    Args:
        root: The scratch directory to build.
        tree: The produced tree to grade.
        oracle_dir: The held-out suite.
    """
    staged_tree = root / ORACLE_TREE_DIR
    shutil.copytree(
        tree,
        staged_tree,
        symlinks=True,
        ignore_dangling_symlinks=True,
    )
    # The oracle is staged BESIDE the tree, so a relative link escaping the
    # tree resolves inside the same mount and `tree/x -> ../oracle` would hand
    # the delivery the suite grading it, with no host access needed.
    drop_escaping_links(staged_tree, anchor=tree)
    # The suite's own sources are staged because collection has to import them,
    # and they are unlinked before the first test body runs. Compiled copies are
    # not staged at all: nothing needs them, they are gitignored so a reviewer
    # never sees them, and the interpreter that produced them left the queries
    # and their expected rows readable in `co_consts`.
    shutil.copytree(
        oracle_dir,
        root / ORACLE_SUITE_DIR,
        ignore=shutil.ignore_patterns(*_NEVER_STAGED),
    )
    (root / INI_NAME).write_text(INI_BODY, encoding="utf-8")


def oracle_argv(*, nodes: dict[str, str], wanted: tuple[str, ...]) -> tuple[str, ...]:
    """Build the argv the oracle container runs.

    Paths are relative to the mounted scratch root, so nothing about the host
    layout travels into the container.

    Returns:
        The arguments after ``python``.
    """
    return (
        "-m",
        "pytest",
        "-c",
        INI_NAME,
        "-p",
        "no:cacheprovider",
        "-q",
        f"--tree={ORACLE_TREE_DIR}",
        f"--report-json={_REPORT_NAME}",
        *(_node_path(Path(ORACLE_SUITE_DIR), nodes[key]) for key in wanted),
    )


def _node_path(oracle_dir: Path, node: str) -> str:
    """Turn a ``file.py::test`` entry into an absolute pytest node id.

    Returns:
        The node id pytest is invoked with.
    """
    path, _, rest = node.partition("::")
    absolute = oracle_dir / path
    return f"{absolute}::{rest}" if rest else str(absolute)


def _read_report(
    report_path: Path,
    *,
    nodes: dict[str, str],
    wanted: tuple[str, ...],
    returncode: int,
) -> dict[str, bool]:
    """Turn the per-node report into a per-requirement verdict.

    A requirement whose node produced no ENTRY counts as failed: the delivery
    did not satisfy it, and pytest declining to collect a node against a tree
    that does not implement it is the ordinary way that happens.

    A missing FILE is a different thing and is refused. ``pytest_sessionfinish``
    writes the report on every completed session, an empty one included, so no
    file at all means pytest died before session end: a collection error, a
    crashed interpreter, a plugin that never loaded. Reading that as every
    requirement failing would render a harness fault as total scientific
    collapse at depth, which is the exact shape of the result being measured.

    Returns:
        The verdict per requested requirement.

    Raises:
        OracleUnusableError: pytest wrote no report at all.
    """
    if not report_path.is_file():
        msg = (
            f"the oracle wrote no report (pytest exited {returncode}), so "
            f"nothing was measured; every completed session writes one, so this "
            f"is a harness fault rather than a failed delivery"
        )
        raise OracleUnusableError(msg)
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    outcomes = {str(key): bool(value) for key, value in raw.items()}
    return {key: outcomes.get(nodes[key], False) for key in wanted}


__all__ = [
    "OracleOutcome",
    "load_index",
    "node_ids",
    "oracle_argv",
    "refuse_if_oracle_survived",
    "requirement_ids",
    "run_oracle",
    "stage",
]
