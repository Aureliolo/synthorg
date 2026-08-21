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
import secrets
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import yaml

from evals.errors import OracleUnusableError
from evals.harness.workspace import drop_escaping_links
from evals.recursion_depth.grading import (
    GRADED_ENV,
    INI_BODY,
    INI_NAME,
    ORACLE_SUITE_DIR,
    ORACLE_TREE_DIR,
    RUNNER_PROBE_ARGS,
    SandboxFactory,
    oracle_fingerprint,
    oracle_leftovers,
    refuse_without_a_runner,
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

#: Where the per-run attribution token is staged for the suite to read.
#:
#: There is ONE mount, so there is no location the graded tree cannot write:
#: the report is a sibling of the tree by necessity, and the sandbox contract
#: offers a single workspace. What can be arranged is that a report the harness
#: did not start is not BELIEVED, and that is what the token does. It is read at
#: import and swept with the expectations before the first test body, so no
#: delivered program ever coexists with the file; a forgery therefore carries no
#: token, and an untokened report refuses the measurement instead of scoring it.
_NONCE_NAME: Final[str] = "run_nonce.txt"

#: Token width. Unguessable is the whole requirement, and this is the width
#: :mod:`secrets` documents for one.
_NONCE_BYTES: Final[int] = 16

#: The pytest exit status meaning every collected test passed.
_PYTEST_OK: Final[int] = 0

#: The pytest exit status meaning tests ran and some failed. Anything else is a
#: fault of the invocation rather than a verdict on the tree.
_PYTEST_TESTS_FAILED: Final[int] = 1

#: What runs the oracle inside the sandbox image, where the bare name is the
#: only interpreter present and is the one the image was built around.
_CONTAINER_INTERPRETER: Final[str] = "python"

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


def declared(index: Mapping[str, object], key: str, *, spec_dir: Path) -> object:
    """Read one required key out of a spec index, or refuse the spec.

    Every malformed-manifest path answers with the SAME typed error, because
    the runner's systemic-versus-cell dispatch keys on the type: an oracle that
    cannot be read at all is true of every remaining cell, so it stops the
    matrix, while a bare ``KeyError`` from one unguarded read is recorded as one
    opaque cell failure and the sweep grinds through the rest producing nothing.

    Args:
        index: The parsed ``requirements.yaml``.
        key: The key the spec must declare.
        spec_dir: The specification directory, for the message.

    Returns:
        The declared value.

    Raises:
        OracleUnusableError: The spec declares no such key.
    """
    if key not in index:
        msg = f"{spec_dir}/requirements.yaml declares no {key}"
        raise OracleUnusableError(msg)
    return index[key]


def requirement_entries(index: Mapping[str, object], *, spec_dir: Path) -> list[object]:
    """Read the requirement list, or refuse the spec.

    Args:
        index: The parsed ``requirements.yaml``.
        spec_dir: The specification directory, for the message.

    Returns:
        The declared requirement entries.

    Raises:
        OracleUnusableError: The spec declares no requirement list.
    """
    entries = declared(index, "requirements", spec_dir=spec_dir)
    if not isinstance(entries, list):
        msg = f"{spec_dir}/requirements.yaml declares no requirement list"
        raise OracleUnusableError(msg)
    return entries


def entry_field(entry: object, field: str, *, spec_dir: Path) -> str:
    """Read one field of one requirement entry, or refuse the spec.

    Args:
        entry: One item of the requirement list.
        field: The field the entry must carry.
        spec_dir: The specification directory, for the message.

    Returns:
        The field's value as text.

    Raises:
        OracleUnusableError: The entry is not a mapping, or omits the field.
    """
    if not isinstance(entry, Mapping):
        msg = f"{spec_dir}/requirements.yaml has a requirement that is not a mapping"
        raise OracleUnusableError(msg)
    fields: Mapping[str, object] = entry
    if field not in fields:
        msg = f"{spec_dir}/requirements.yaml has a requirement with no {field}"
        raise OracleUnusableError(msg)
    return str(fields[field])


def requirement_ids(spec_dir: Path) -> tuple[str, ...]:
    """Every requirement id the spec declares, in declaration order.

    Args:
        spec_dir: The specification directory.

    Returns:
        The requirement ids.

    Raises:
        OracleUnusableError: The spec's index is malformed.
    """
    entries = requirement_entries(load_index(spec_dir), spec_dir=spec_dir)
    return tuple(entry_field(entry, "id", spec_dir=spec_dir) for entry in entries)


def node_ids(spec_dir: Path) -> dict[str, str]:
    """Map each requirement id to the oracle node that decides it.

    Args:
        spec_dir: The specification directory.

    Returns:
        The requirement-to-node map.

    Raises:
        OracleUnusableError: The spec's index is malformed.
    """
    entries = requirement_entries(load_index(spec_dir), spec_dir=spec_dir)
    return {
        entry_field(entry, "id", spec_dir=spec_dir): entry_field(
            entry, "oracle", spec_dir=spec_dir
        )
        for entry in entries
    }


async def run_oracle(
    *,
    build_sandbox: SandboxFactory,
    spec_dir: Path,
    tree: Path,
    only: frozenset[str] | None = None,
    interpreter: str = _CONTAINER_INTERPRETER,
) -> OracleOutcome:
    """Grade *tree* against the spec's held-out oracle, in a container.

    Args:
        build_sandbox: Builds the container backend the grading runs in, rooted
            at the scratch directory this assembles.
        spec_dir: The specification directory.
        tree: The produced tree to grade.
        only: Restrict the run to these requirement ids. ``None`` runs all of
            them, which is what the final merged tree is graded by.
        interpreter: What runs ``-m pytest``. Defaults to the bare name, which
            is correct inside the sandbox image and is where a recording runs.
            A caller grading through a HOST-side sandbox passes
            ``sys.executable``: the bare name is resolved on PATH there, which
            on a Linux runner finds the system interpreter rather than this
            project's environment, and that interpreter has no pytest.

    Returns:
        The outcome, with one verdict per requirement asked about.

    Raises:
        OracleUnusableError: pytest could not run the oracle at all, so there
            is no verdict to record.
        EvalToolMissingError: The interpreter has no pytest, which is systemic
            and stops the matrix.
    """
    index = load_index(spec_dir)
    nodes = node_ids(spec_dir)
    wanted = tuple(key for key in nodes if only is None or key in only)
    if not wanted:
        return OracleOutcome(results={}, report="")
    oracle_dir = spec_dir / str(declared(index, "oracle_dir", spec_dir=spec_dir))
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        await asyncio.to_thread(stage, root, tree=tree, oracle_dir=oracle_dir)
        nonce = secrets.token_hex(_NONCE_BYTES)
        (root / ORACLE_SUITE_DIR / _NONCE_NAME).write_text(nonce, encoding="utf-8")
        # Taken here, on the host, before anything the tree wrote can run. The
        # token is deliberately outside it: the fingerprint covers what has to
        # survive the run unchanged, and the token has to be gone by the time
        # the first test body runs.
        staged_before = await asyncio.to_thread(
            oracle_fingerprint, root / ORACLE_SUITE_DIR
        )
        report_path = root / _REPORT_NAME
        # Probed BEFORE the tree runs, because the answer stops the whole
        # matrix and the graded run's own output is agent-authored. The
        # returncode of the run itself cannot substitute either: an interpreter
        # with no pytest exits 1, which is exactly what "tests ran and some
        # failed" looks like, so every requirement would be recorded as failed
        # and the sweep would publish a curve of zeros that reads as a
        # catastrophic result rather than as a harness that never ran anything.
        refuse_without_a_runner(
            await build_sandbox(root).execute(
                command=interpreter,
                args=RUNNER_PROBE_ARGS,
                cwd=root,
                env_overrides=GRADED_ENV,
                timeout=_ORACLE_TIMEOUT_SECONDS,
                category=ToolCategory.CODE_EXECUTION.value,
            )
        )
        result = await build_sandbox(root).execute(
            command=interpreter,
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
        await asyncio.to_thread(refuse_if_oracle_inputs_changed, root, staged_before)
        results = _read_report(
            report_path,
            nodes=nodes,
            wanted=wanted,
            returncode=result.returncode,
            nonce=nonce,
        )
    logger.info(
        EVALS_RECURSION_ORACLE_RUN,
        tree=str(tree),
        requested=len(wanted),
        passed=sum(1 for ok in results.values() if ok),
    )
    return OracleOutcome(results=results, report=report)


def refuse_if_oracle_inputs_changed(root: Path, before: str) -> None:
    """Refuse a measurement taken against inputs the tree may have rewritten.

    The delivered program is spawned by a test body, in the same mount and with
    the same identity as pytest, so the fixtures under ``oracle/data/`` are
    writable by it. Those fixtures are what every query is RUN AGAINST, and a
    delivery that edited one to match whatever it produces would be scored on
    its own answer with nothing else noticing: the deletion sweep reads names,
    not bytes.

    Isolating them properly means a second mount namespace per spawn, which the
    oracle deliberately does not build (it runs inside one container already,
    and a nested sandbox per invocation would multiply a 42-requirement grading
    by an interpreter start each). Detection is the cheaper half and is enough:
    a measurement taken against altered inputs is refused rather than recorded,
    so the attack buys a stopped run instead of a better score.

    Args:
        root: The scratch directory the grading ran in.
        before: The digest taken before the container started.

    Raises:
        OracleUnusableError: The retained oracle inputs are not what they were.
    """
    if oracle_fingerprint(root / ORACLE_SUITE_DIR) == before:
        return
    msg = (
        "the oracle's own inputs changed while the graded tree was running, so "
        "the delivery was scored against fixtures it may have written itself "
        "and this measurement cannot be trusted"
    )
    raise OracleUnusableError(msg)


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
        *(_node_path(PurePosixPath(ORACLE_SUITE_DIR), nodes[key]) for key in wanted),
    )


def _node_path(oracle_dir: PurePosixPath, node: str) -> str:
    """Turn a ``file.py::test`` entry into a pytest node id for the container.

    POSIX explicitly, because the separator belongs to the machine that RUNS
    the argument, not the one that builds it. A plain ``Path`` renders the
    Windows separator when the recorder runs there, and that character is
    ordinary text in the Linux container: pytest resolves none of the
    arguments, so it never loads the suite's ``conftest.py`` as an initial
    conftest, and the run dies at argument parsing on the ``--tree`` and
    ``--report-json`` options that conftest is what registers. The oracle then
    grades nothing, on every tree, having never run a single test. A POSIX
    runner cannot reproduce it, so CI stayed green throughout.

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
    nonce: str,
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

    So is a report this run cannot be shown to have produced. It sits in the
    single mount the graded tree also runs in, so nothing about its LOCATION
    keeps a delivery out of it, and pytest writing last only means a forgery
    has to outlive the session rather than that it cannot happen. What the tree
    cannot do is name the token, which stopped being readable before its first
    process existed, so an unattributable report is refused rather than scored:
    the failure it would otherwise produce is verdicts that look exactly like
    honest ones.

    Returns:
        The verdict per requested requirement.

    Raises:
        OracleUnusableError: pytest wrote no report at all, or wrote one this
            run cannot be shown to have produced.
    """
    if not report_path.is_file():
        msg = (
            f"the oracle wrote no report (pytest exited {returncode}), so "
            f"nothing was measured; every completed session writes one, so this "
            f"is a harness fault rather than a failed delivery"
        )
        raise OracleUnusableError(msg)
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"the oracle's report against {report_path} did not parse as JSON"
        raise OracleUnusableError(msg) from exc
    if not isinstance(raw, dict):
        # The report sits in the mount the graded tree runs under, so its
        # SHAPE is not guaranteed by the conftest that writes it. A list or a
        # bare string would raise `AttributeError` on `.items()` below and the
        # runner would file that as an opaque cell failure rather than as the
        # unusable measurement it is.
        msg = (
            f"the oracle's report is a {type(raw).__name__} rather than a "
            f"mapping of node to outcome, so nothing can be scored from it"
        )
        raise OracleUnusableError(msg)
    carried = raw.get("nonce")
    # Typed and narrowed to ASCII BEFORE the comparison, because
    # `compare_digest` raises `TypeError` on a str holding any non-ASCII
    # character, and the value it is handed here is one a forger writes. Coerced
    # with `str()` instead, a report carrying "é" crashes the read rather than
    # refusing it, and the runner files a forgery as an opaque cell failure. The
    # token is hex, so nothing excluded here could have matched anyway: this
    # reaches the same verdict without the crash.
    #
    # Compared in constant time because the comparison is against a value an
    # attacker supplies, which is the situation the timing-safe form exists for.
    if (
        not isinstance(carried, str)
        or not carried.isascii()
        or not secrets.compare_digest(carried, nonce)
    ):
        msg = (
            f"the oracle's report at {report_path} does not carry this run's "
            f"token, so it is not the one this session wrote; the graded tree "
            f"shares the mount it sits in, and verdicts of unknown authorship "
            f"read exactly like measured ones"
        )
        raise OracleUnusableError(msg)
    recorded = raw.get("outcomes")
    if not isinstance(recorded, Mapping):
        msg = (
            f"the oracle's report carries a {type(recorded).__name__} of "
            f"outcomes rather than a mapping of node to outcome, so nothing "
            f"can be scored from it"
        )
        raise OracleUnusableError(msg)
    outcomes: dict[str, bool] = {}
    for key, value in recorded.items():
        # Refused rather than coerced. `bool("false")` is True and `bool(1)` is
        # True, so a report whose verdicts are the wrong TYPE would be read as a
        # requirement passing, which is the one direction that inflates the
        # measurement. The suite writes real booleans, so anything else says the
        # report is not the shape this run's own conftest produces.
        if not isinstance(value, bool):
            msg = (
                f"the oracle's report records a {type(value).__name__} rather "
                f"than a verdict for {key!r}, so nothing can be scored from it"
            )
            raise OracleUnusableError(msg)
        outcomes[str(key)] = value
    return {key: outcomes.get(nodes[key], False) for key in wanted}


__all__ = [
    "OracleOutcome",
    "load_index",
    "node_ids",
    "oracle_argv",
    "refuse_if_oracle_inputs_changed",
    "refuse_if_oracle_survived",
    "requirement_ids",
    "run_oracle",
    "stage",
]
