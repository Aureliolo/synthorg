# module-kind: tests
"""The argv the oracle hands its container is POSIX on every host."""

from pathlib import Path

import pytest

from evals.recursion_depth.grading import ORACLE_SUITE_DIR
from evals.recursion_depth.oracle import node_ids, oracle_argv

pytestmark = pytest.mark.unit

_SPEC_DIR = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "recursion_depth"
    / "spec"
    / "sqlcsv"
)


def _node_arguments() -> tuple[str, ...]:
    """Build the argv and return only its node-id arguments.

    Returns:
        Every argument that names a test node.
    """
    nodes = node_ids(_SPEC_DIR)
    argv = oracle_argv(nodes=nodes, wanted=tuple(nodes))
    return tuple(arg for arg in argv if arg.startswith(ORACLE_SUITE_DIR))


def test_node_arguments_never_carry_a_backslash() -> None:
    """A Windows separator here silently grades nothing, on every tree.

    The container is Linux, where a backslash is an ordinary character rather
    than a separator: pytest resolves none of the arguments, so it never loads
    the suite's ``conftest.py``, and the run dies at argument parsing on the
    options that conftest registers. Asserted on the SEPARATOR rather than on
    behaviour because the fault cannot reproduce on a POSIX runner, so a
    behavioural test would pass in CI while the recorder stayed broken.
    """
    offenders = [argument for argument in _node_arguments() if "\\" in argument]

    assert not offenders, f"{len(offenders)} node arguments carry a backslash"


def test_node_arguments_are_rooted_in_the_staged_suite() -> None:
    """Each node id addresses the suite where ``stage`` puts it."""
    arguments = _node_arguments()

    assert arguments, "the spec declares no requirements to grade"
    for argument in arguments:
        assert argument.startswith(f"{ORACLE_SUITE_DIR}/")
        assert "::" in argument
