# module-kind: tests
"""The oracle has been observed to pass a correct delivery, and to fail an empty one.

Two claims, and the experiment is worthless without either.

The first is that the spec is satisfiable as written: a reference delivery
built from `SPEC.md` alone passes all 42 requirements. Without this, the first
thing a real recording discovers is a requirement no implementation can meet,
after thousands of agent sessions have been paid for, and every survival figure
would be a measurement of the oracle rather than of the merge.

The second is that the oracle discriminates: an empty tree fails every
requirement. An oracle that passes nothing tells you the same story as one that
passes everything.
"""

import asyncio
import shutil
from pathlib import Path

import pytest
import yaml

from evals.errors import OracleUnusableError
from evals.recursion_depth.grading import (
    GRADED_ENV,
    ORACLE_SUITE_DIR,
    oracle_leftovers,
)
from evals.recursion_depth.oracle import (
    OracleOutcome,
    load_index,
    node_ids,
    oracle_argv,
    refuse_if_oracle_survived,
    requirement_ids,
    run_oracle,
    stage,
)
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox

pytestmark = [
    pytest.mark.integration,
    # Each requirement is a subprocess; the suite is dozens of interpreter
    # starts rather than anything slow in itself.
    pytest.mark.slow,
    pytest.mark.timeout(600),
]

_SPEC_DIR = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "recursion_depth"
    / "spec"
    / "sqlcsv"
)
_REFERENCE_TREE = Path(__file__).resolve().parent / "reference_tree"


def _local_sandbox(root: Path) -> SubprocessSandbox:
    """Build the backend these two tests grade in.

    A recording grades in a container, because the tree it grades is model
    output. Here the tree is either this repository's own committed reference
    delivery or an empty directory, so the reason for the container does not
    apply and requiring a Docker daemon would make the one check that the spec
    is satisfiable at all the hardest check in the suite to run.
    ``SubprocessSandbox`` still filters the environment through its own
    allowlist, so nothing here inherits the host's.

    It follows that the deletion test below validates the DELETION, not the
    container: it proves the expectations are gone from the staged directory
    before any test body runs, which is the property that matters and is the same
    under either backend. That the graded run is confined to a container is a
    separate claim, and it rests on :mod:`evals.harness.binding` rather than on
    anything observed here.

    Returns:
        A sandbox rooted at *root*.
    """
    return SubprocessSandbox(workspace=root)


@pytest.fixture(scope="module")
def reference_outcome() -> OracleOutcome:
    """Grade the reference delivery once for every assertion below.

    Driven with ``asyncio.run`` rather than declared async: the grading is a
    module-scoped fixture and an async one would need a module-scoped event
    loop to match, which is a pytest-asyncio scoping argument this test has no
    stake in. Nothing here shares a loop with anything.

    Returns:
        What the oracle made of the reference tree.
    """
    return asyncio.run(
        run_oracle(
            build_sandbox=_local_sandbox, spec_dir=_SPEC_DIR, tree=_REFERENCE_TREE
        )
    )


def test_the_reference_delivery_passes_every_requirement(
    reference_outcome: OracleOutcome,
) -> None:
    failed = sorted(reference_outcome.failed)
    assert failed == [], (
        f"the spec is not satisfiable as written: {failed}\n{reference_outcome.report}"
    )


def test_every_declared_requirement_was_actually_run(
    reference_outcome: OracleOutcome,
) -> None:
    # A requirement whose oracle node id has drifted is silently never run, and
    # silently never fails, so it would read as passed for ever.
    declared = set(requirement_ids(_SPEC_DIR))

    assert set(reference_outcome.results) == declared


def test_the_declared_requirements_match_the_spec_document() -> None:
    # The brief an agent reads and the index the harness scores from are two
    # files, and a requirement in one and not the other is either work nobody
    # asked for or a claim nobody can make.
    index = yaml.safe_load((_SPEC_DIR / "requirements.yaml").read_text("utf-8"))
    declared = {entry["id"] for entry in index["requirements"]}
    prose = (_SPEC_DIR / "SPEC.md").read_text("utf-8")

    missing = {identifier for identifier in declared if identifier not in prose}
    assert missing == set()


def test_the_expectations_do_not_outlive_collection(tmp_path: Path) -> None:
    # The load-bearing property of the whole measurement. Grading puts the tree
    # and the oracle in one mount by necessity, so one `..` from the delivered
    # program's working directory would reach the expected outputs unless they
    # are gone by the time it runs. Inspected on the staged directory directly:
    # the run's own report is length-bounded, so a marker printed by a delivery
    # is not a channel this can rely on.
    #
    # Asserted through the ALLOWLIST rather than by globbing the shapes anyone
    # thought of. The version of this that checked `test_*.py` passed while the
    # compiled modules sat next to the graded tree carrying the same queries and
    # expected rows, so a test written to the implementation's own predicate is
    # exactly as blind as the implementation.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    oracle_dir = _SPEC_DIR / str(load_index(_SPEC_DIR)["oracle_dir"])
    stage(scratch, tree=_REFERENCE_TREE, oracle_dir=oracle_dir)
    staged = scratch / ORACLE_SUITE_DIR
    assert oracle_leftovers(staged) != ()

    result = asyncio.run(
        _local_sandbox(scratch).execute(
            command="python",
            args=oracle_argv(
                nodes=node_ids(_SPEC_DIR), wanted=tuple(node_ids(_SPEC_DIR))[:2]
            ),
            cwd=scratch,
            env_overrides=dict(GRADED_ENV),
            timeout=300.0,
        )
    )

    assert result.returncode == 0, result.stdout
    assert oracle_leftovers(staged) == ()
    # What stays is what pytest re-reads during setup, and neither holds an
    # expected output.
    assert (staged / "conftest.py").is_file()
    assert (staged / "__init__.py").is_file()


def test_compiled_expectations_are_never_staged(tmp_path: Path) -> None:
    # The staged copy is taken from a directory the recorder's own machine has
    # been running the suite in, so it holds a `__pycache__` that is gitignored
    # and therefore invisible to everyone reviewing the copy. Planted here
    # rather than relied upon, because whether one exists depends on what the
    # machine happened to do before the sweep ran.
    source = tmp_path / "oracle_src"
    shutil.copytree(_SPEC_DIR / str(load_index(_SPEC_DIR)["oracle_dir"]), source)
    cache = source / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "test_planted.cpython-314.pyc").write_bytes(b"expected rows live here")
    empty_tree = tmp_path / "tree"
    empty_tree.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    stage(scratch, tree=empty_tree, oracle_dir=source)

    assert list((scratch / ORACLE_SUITE_DIR).rglob("*.pyc")) == []


@pytest.mark.parametrize(
    "leftover",
    [
        "test_leftover.py",
        # Refused on the same footing as source: it holds the same assertions,
        # and `marshal` reads them back out without needing the interpreter.
        "__pycache__/test_leftover.cpython-314.pyc",
    ],
)
def test_a_surviving_expectation_refuses_the_measurement(
    tmp_path: Path, leftover: str
) -> None:
    # The harness's own half of the guard: if the suite ever stops deleting
    # itself, verdicts would keep looking exactly like the honest ones.
    survivor = tmp_path / ORACLE_SUITE_DIR / leftover
    survivor.parent.mkdir(parents=True)
    survivor.write_bytes(b"")

    with pytest.raises(OracleUnusableError, match="outlived its own collection"):
        refuse_if_oracle_survived(tmp_path)


def test_the_allowlist_admits_only_what_setup_and_the_query_need(
    tmp_path: Path,
) -> None:
    # The complement of the refusal: a sweep that removed everything would also
    # pass `oracle_leftovers`, and the run would then die during setup instead
    # of grading. Both keepers and the fixture data have to survive it.
    suite = tmp_path / ORACLE_SUITE_DIR
    (suite / "data" / "shop").mkdir(parents=True)
    (suite / "conftest.py").write_text("", encoding="utf-8")
    (suite / "__init__.py").write_text("", encoding="utf-8")
    (suite / "data" / "shop" / "orders.csv").write_text("id\n1\n", encoding="utf-8")

    assert oracle_leftovers(suite) == ()


async def test_an_empty_tree_fails_every_requirement(tmp_path: Path) -> None:
    outcome = await run_oracle(
        build_sandbox=_local_sandbox, spec_dir=_SPEC_DIR, tree=tmp_path
    )

    assert outcome.passed == frozenset()
    assert outcome.failed == frozenset(requirement_ids(_SPEC_DIR))
