#!/usr/bin/env python3
# module-kind: code
"""AppState attribute lock.

Requires :class:`synthorg.api.state.AppState` to declare an EMPTY
``__slots__``. The composition root carries no direct mutable slots: every
piece of application state lives either on a feature state slice
(``AppStateSliceMixin``) or on a cohesive primitive owner object composed
onto ``AppState`` (``bridge_config`` / ``per_op_limits`` / ``request_locks``
/ ``ws_auth_limits``). Adding any slot back to ``AppState`` fails this gate.

The approved set lives inside this gate as :data:`APPROVED_SLOTS` (now
empty). Re-introducing a slot is a deliberate decision: the contributor
would have to edit this gate, which makes the regression visible in the
diff and forces a conversation about whether the state belongs on a slice
or an owner instead of the central composition root.

Run from the repo root::

    uv run python scripts/check_no_implicit_state_attribute.py
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_STATE_PY_REL: Final[str] = "src/synthorg/api/state.py"
_APP_STATE_CLASS: Final[str] = "AppState"

APPROVED_SLOTS: Final[frozenset[str]] = frozenset()
"""Slots ``AppState`` may declare directly: none.

``AppState`` is a thin composition root. Its lifecycle identity (clock,
config, uptime baseline, shutdown event, background-task sets) lives in
``__dict__``; its cross-cutting mutable primitives live on cohesive owner
objects (``bridge_config`` / ``per_op_limits`` / ``request_locks`` /
``ws_auth_limits``); every domain service lives on a feature state slice
(``BaseFeatureStateSlice``). The empty set is the contract the gate
enforces: no state may be bolted back onto the composition root's slots.
"""


def extract_slots(state_py: Path) -> frozenset[str]:
    """Extract the ``__slots__`` declared on :class:`AppState`.

    AST-only parse. Returns the empty frozenset only when *state_py* has no
    ``AppState`` class or its ``__slots__`` is missing / not a literal tuple
    of strings; parse failures on the target file propagate so the operator
    sees the real cause rather than a misleading "slots changed" finding.

    Args:
        state_py: Path to the module declaring ``AppState``.

    Returns:
        Set of slot names AppState declares.

    Raises:
        OSError: Cannot read *state_py*.
        SyntaxError: *state_py* is not valid Python.
        UnicodeDecodeError: *state_py* is not valid UTF-8.
    """
    tree = ast.parse(state_py.read_text(encoding="utf-8"), filename=str(state_py))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == _APP_STATE_CLASS:
            return _slots_from_class(node)
    return frozenset()


def _slots_from_class(node: ast.ClassDef) -> frozenset[str]:
    """Return the ``__slots__`` literal declared on *node*, if any."""
    for stmt in node.body:
        targets = _assign_targets(stmt)
        if not targets:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "__slots__"
            for target in targets
        ):
            value = _stmt_value(stmt)
            return _string_tuple(value)
    return frozenset()


def _assign_targets(stmt: ast.stmt) -> list[ast.expr]:
    """Return the assignment targets for an Assign / AnnAssign, else []."""
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.target]
    if isinstance(stmt, ast.Assign):
        return list(stmt.targets)
    return []


def _stmt_value(stmt: ast.stmt) -> ast.expr | None:
    """Return the RHS expression of an Assign / AnnAssign, else None."""
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        return stmt.value
    return None


def _string_tuple(value: ast.expr | None) -> frozenset[str]:
    """Return string elements of a tuple/list literal, else frozenset()."""
    if not isinstance(value, (ast.Tuple, ast.List)):
        return frozenset()
    names: set[str] = set()
    for element in value.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            names.add(element.value)
    return frozenset(names)


def check(*, state_py: Path) -> list[str]:
    """Run the gate; return findings (empty == AppState matches the contract)."""
    actual = extract_slots(state_py)
    findings: list[str] = []
    added = actual - APPROVED_SLOTS
    removed = APPROVED_SLOTS - actual
    if added:
        findings.append(
            "AppState.__slots__ must stay empty but declares "
            f"({sorted(added)}). AppState is a thin composition root: move "
            "this state onto a feature state slice (BaseFeatureStateSlice "
            "subclass) or a cohesive primitive owner object (e.g. "
            "bridge_config / per_op_limits / request_locks / ws_auth_limits) "
            "instead of bolting a slot onto the root."
        )
    if removed:
        findings.append(
            "AppState.__slots__ no longer carries approved attributes "
            f"({sorted(removed)}). The gate is stale; remove them from "
            "APPROVED_SLOTS in scripts/check_no_implicit_state_attribute.py."
        )
    return findings


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lock AppState's slot set.")
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT_DEFAULT)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on clean tree, 1 on any violation."""
    args = _build_arg_parser().parse_args(argv)
    state_py: Path = args.repo_root.resolve() / _STATE_PY_REL
    findings = check(state_py=state_py)
    if not findings:
        return 0
    print("AppState attribute lock findings:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
