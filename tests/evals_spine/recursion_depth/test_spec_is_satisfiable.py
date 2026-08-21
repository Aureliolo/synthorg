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
from pathlib import Path

import pytest
import yaml

from evals.errors import OracleUnusableError
from evals.recursion_depth.grading import GRADED_ENV, ORACLE_SUITE_DIR
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
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    oracle_dir = _SPEC_DIR / str(load_index(_SPEC_DIR)["oracle_dir"])
    stage(scratch, tree=_REFERENCE_TREE, oracle_dir=oracle_dir)
    staged = scratch / ORACLE_SUITE_DIR
    assert sorted(p.name for p in staged.rglob("test_*.py")) != []

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
    assert sorted(p.name for p in staged.rglob("test_*.py")) == []
    # What stays is what pytest re-reads during setup, and neither holds an
    # expected output.
    assert (staged / "conftest.py").is_file()
    assert (staged / "__init__.py").is_file()


def test_a_surviving_expectation_refuses_the_measurement(tmp_path: Path) -> None:
    # The harness's own half of the guard: if the suite ever stops deleting
    # itself, verdicts would keep looking exactly like the honest ones.
    (tmp_path / ORACLE_SUITE_DIR).mkdir()
    (tmp_path / ORACLE_SUITE_DIR / "test_leftover.py").write_text("", encoding="utf-8")

    with pytest.raises(OracleUnusableError, match="outlived its own collection"):
        refuse_if_oracle_survived(tmp_path)


async def test_an_empty_tree_fails_every_requirement(tmp_path: Path) -> None:
    outcome = await run_oracle(
        build_sandbox=_local_sandbox, spec_dir=_SPEC_DIR, tree=tmp_path
    )

    assert outcome.passed == frozenset()
    assert outcome.failed == frozenset(requirement_ids(_SPEC_DIR))
