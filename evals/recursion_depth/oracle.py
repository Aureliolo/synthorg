# module-kind: code
"""Run the held-out oracle against a produced tree.

The oracle lives outside every workspace and is named in no brief. It is run
from here, after the fact, as a subprocess so the graded tree's own dependencies
and its own conftest cannot reach into this process.

The invocation deliberately clears the repository's ``addopts``. Inherited, they
would fan the run across eight xdist workers, install the typeguard import hook
over ``synthorg``, and apply a 30-second per-test timeout, none of which has
anything to do with grading a delivered CLI, and the first of which would make
the per-requirement result impossible to attribute.
"""

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from evals.errors import OracleUnusableError
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_ORACLE_RUN

logger = get_logger(__name__)

#: Long enough for dozens of interpreter starts against a slow tree, short
#: enough that a delivery which deadlocks fails the run rather than holding it.
_ORACLE_TIMEOUT_SECONDS: Final[float] = 900.0

#: The pytest exit status meaning every collected test passed.
_PYTEST_OK: Final[int] = 0

#: The pytest exit status meaning tests ran and some failed. Anything else is a
#: fault of the invocation rather than a verdict on the tree.
_PYTEST_TESTS_FAILED: Final[int] = 1


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


def _node_ids(spec_dir: Path) -> dict[str, str]:
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


def run_oracle(
    *,
    spec_dir: Path,
    tree: Path,
    only: frozenset[str] | None = None,
) -> OracleOutcome:
    """Grade *tree* against the spec's held-out oracle.

    Args:
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
    nodes = _node_ids(spec_dir)
    wanted = tuple(key for key in nodes if only is None or key in only)
    if not wanted:
        return OracleOutcome(results={}, report="")
    oracle_dir = spec_dir / str(load_index(spec_dir)["oracle_dir"])
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.json"
        completed = _invoke(
            tree=tree,
            node_ids=tuple(_node_path(oracle_dir, nodes[key]) for key in wanted),
            report_path=report_path,
        )
        results = _read_report(report_path, nodes=nodes, wanted=wanted)
    report = completed.stdout + completed.stderr
    if completed.returncode not in (_PYTEST_OK, _PYTEST_TESTS_FAILED):
        msg = (
            f"the oracle could not be run against {tree} "
            f"(pytest exited {completed.returncode}):\n{report}"
        )
        raise OracleUnusableError(msg)
    logger.info(
        EVALS_RECURSION_ORACLE_RUN,
        tree=str(tree),
        requested=len(wanted),
        passed=sum(1 for ok in results.values() if ok),
    )
    return OracleOutcome(results=results, report=report)


def _node_path(oracle_dir: Path, node: str) -> str:
    """Turn a ``file.py::test`` entry into an absolute pytest node id.

    Returns:
        The node id pytest is invoked with.
    """
    path, _, rest = node.partition("::")
    absolute = oracle_dir / path
    return f"{absolute}::{rest}" if rest else str(absolute)


def _invoke(
    *,
    tree: Path,
    node_ids: tuple[str, ...],
    report_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run pytest over the oracle nodes and return what it did.

    Returns:
        The completed process.

    Raises:
        OracleUnusableError: pytest did not finish inside its ceiling.
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        # The repository's own addopts have nothing to do with grading a
        # delivered CLI, and xdist in particular would scatter the
        # per-requirement result across workers.
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-q",
        f"--tree={tree}",
        f"--report-json={report_path}",
        *node_ids,
    ]
    try:
        return subprocess.run(  # noqa: S603 -- interpreter path, fixed argv
            argv,
            capture_output=True,
            text=True,
            timeout=_ORACLE_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = (
            f"the oracle did not finish inside {_ORACLE_TIMEOUT_SECONDS}s against "
            f"{tree}; a delivery that deadlocks is a failed requirement, but a "
            "run that cannot report which one is not a measurement"
        )
        raise OracleUnusableError(msg) from exc


def _read_report(
    report_path: Path, *, nodes: dict[str, str], wanted: tuple[str, ...]
) -> dict[str, bool]:
    """Turn the per-node report into a per-requirement verdict.

    A requirement whose node produced no entry counts as failed: the delivery
    did not satisfy it, and pytest declining to collect a node against an empty
    tree is the ordinary way that happens.

    Returns:
        The verdict per requested requirement.
    """
    if not report_path.is_file():
        return dict.fromkeys(wanted, False)
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    outcomes = {str(key): bool(value) for key, value in raw.items()}
    return {key: outcomes.get(nodes[key], False) for key in wanted}


__all__ = [
    "OracleOutcome",
    "load_index",
    "requirement_ids",
    "run_oracle",
]
