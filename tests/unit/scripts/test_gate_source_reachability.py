"""Tests for the shared gate helper's dead-code analysis.

Every behavioural gate that asks "is this enforcement statement present"
answers it through ``reachable_statements``. A statement the helper yields
but control flow can never reach lets a DEAD check satisfy a gate, so the
termination rules below are the security-relevant part of the helper.
"""

import ast
import importlib.util
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _GateSourceModule(Protocol):
    """Subset of ``scripts/_gate_source.py`` under test."""

    @staticmethod
    def reachable_statements(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]: ...

    @staticmethod
    def statement_expressions(stmt: ast.stmt) -> Iterator[ast.AST]: ...


def _load_module() -> _GateSourceModule:
    script_path = _REPO_ROOT / "scripts" / "_gate_source.py"
    spec = importlib.util.spec_from_file_location("_gate_source", script_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateSourceModule, module)


_MODULE = _load_module()

_TRAILING_MARKER = "dead-marker"


def _reaches_trailing_call(source: str) -> bool:
    """Whether the helper still yields the trailing marker statement.

    The fixtures below all end with ``marker("dead-marker")``; whether that
    call survives the walk is exactly what each rule decides. Paired with
    ``statement_expressions`` rather than a bare ``ast.walk`` because that
    is how every consuming gate reads the helper: walking a yielded
    ``FunctionDef`` directly would re-enter the nested body the helper
    deliberately refuses to traverse.

    Returns:
        ``True`` when the marker call is reachable per the helper.
    """
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return any(
        isinstance(node, ast.Constant) and node.value == _TRAILING_MARKER
        for stmt in _MODULE.reachable_statements(function.body)
        for node in _MODULE.statement_expressions(stmt)
    )


class TestTerminatingStatements:
    """Shapes after which nothing in the same block can run."""

    @pytest.mark.parametrize(
        "terminator",
        [
            'return "x"',
            "raise ValueError(1)",
        ],
    )
    def test_direct_terminator_ends_the_block(self, terminator: str) -> None:
        source = f'def f():\n    {terminator}\n    marker("{_TRAILING_MARKER}")\n'
        assert _reaches_trailing_call(source) is False

    def test_if_else_with_both_branches_returning(self) -> None:
        source = f"""\
def f(cond):
    if cond:
        return 1
    else:
        return 2
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is False

    def test_try_with_every_path_returning(self) -> None:
        source = f"""\
def f():
    try:
        return 1
    except ValueError:
        return 2
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is False

    def test_try_whose_finally_returns(self) -> None:
        source = f"""\
def f():
    try:
        pass
    finally:
        return 2
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is False

    def test_with_whose_body_returns(self) -> None:
        source = f"""\
def f(cm):
    with cm:
        return 1
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is False

    def test_match_with_catch_all_and_every_case_returning(self) -> None:
        source = f"""\
def f(value):
    match value:
        case 1:
            return "one"
        case _:
            return "other"
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is False

    def test_while_true_without_a_break(self) -> None:
        source = f"""\
def f(step):
    while True:
        step()
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is False


class TestNonTerminatingStatements:
    """Shapes that fall through, so a following statement stays live.

    Claiming termination here would drop a live enforcement statement and
    fail a gate its target actually satisfies.
    """

    def test_if_without_else(self) -> None:
        source = f"""\
def f(cond):
    if cond:
        return 1
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is True

    def test_if_else_with_one_branch_falling_through(self) -> None:
        source = f"""\
def f(cond):
    if cond:
        return 1
    else:
        pass
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is True

    def test_try_whose_handler_falls_through(self) -> None:
        source = f"""\
def f():
    try:
        return 1
    except ValueError:
        pass
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is True

    def test_try_whose_body_falls_through(self) -> None:
        source = f"""\
def f(step):
    try:
        step()
    except ValueError:
        return 2
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is True

    def test_match_without_a_catch_all(self) -> None:
        source = f"""\
def f(value):
    match value:
        case 1:
            return "one"
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is True

    def test_while_true_with_a_break(self) -> None:
        source = f"""\
def f(step):
    while True:
        if step():
            break
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is True

    def test_guarded_match_catch_all_is_not_a_catch_all(self) -> None:
        source = f"""\
def f(value):
    match value:
        case _ if value:
            return "truthy"
    marker("{_TRAILING_MARKER}")
"""
        assert _reaches_trailing_call(source) is True


class TestStaticallyDeadBranches:
    """A branch a constant condition can never enter is not traversed.

    A scoper call parked in ``if False:`` would otherwise satisfy a gate
    while the live path uses an unscoped capability set.
    """

    def test_if_false_body_is_skipped(self) -> None:
        source = f"""\
def f():
    if False:
        marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is False

    def test_if_true_else_is_skipped(self) -> None:
        source = f"""\
def f():
    if True:
        pass
    else:
        marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is False

    def test_while_false_body_is_skipped(self) -> None:
        source = f"""\
def f(step):
    while False:
        marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is False

    def test_if_true_body_stays_live(self) -> None:
        source = f"""\
def f():
    if True:
        marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is True

    def test_if_false_else_stays_live(self) -> None:
        source = f"""\
def f():
    if False:
        pass
    else:
        marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is True

    def test_non_constant_condition_stays_live(self) -> None:
        source = f"""\
def f(cond):
    if cond:
        marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is True

    def test_while_false_else_stays_live(self) -> None:
        # ``else`` runs on normal completion, immediately for a false test.
        source = f"""\
def f():
    while False:
        pass
    else:
        marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is True


class TestNestedScopes:
    """A nested ``def`` body belongs to that helper, not this scope."""

    def test_nested_function_body_is_not_traversed(self) -> None:
        source = f"""\
def f():
    def inner():
        marker("{_TRAILING_MARKER}")

    return inner
"""
        assert _reaches_trailing_call(source) is False

    def test_statement_inside_a_live_match_case_is_yielded(self) -> None:
        source = f"""\
def f(value):
    match value:
        case 1:
            marker("{_TRAILING_MARKER}")
    return None
"""
        assert _reaches_trailing_call(source) is True
