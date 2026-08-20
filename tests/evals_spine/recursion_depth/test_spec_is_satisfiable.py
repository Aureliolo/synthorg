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

from pathlib import Path

import pytest
import yaml

from evals.recursion_depth.oracle import (
    OracleOutcome,
    requirement_ids,
    run_oracle,
)

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


@pytest.fixture(scope="module")
def reference_outcome() -> OracleOutcome:
    """Grade the reference delivery once for every assertion below.

    Returns:
        What the oracle made of the reference tree.
    """
    return run_oracle(spec_dir=_SPEC_DIR, tree=_REFERENCE_TREE)


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


def test_an_empty_tree_fails_every_requirement(tmp_path: Path) -> None:
    outcome = run_oracle(spec_dir=_SPEC_DIR, tree=tmp_path)

    assert outcome.passed == frozenset()
    assert outcome.failed == frozenset(requirement_ids(_SPEC_DIR))
