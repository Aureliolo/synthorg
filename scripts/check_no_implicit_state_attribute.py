#!/usr/bin/env python3
# module-kind: code
"""AppState attribute lock.

Forbids growing :class:`synthorg.api.state.AppState`'s ``__slots__`` past
the hard-coded approved set. Any new application state must go through a
feature state slice (``AppStateSliceMixin``) rather than being bolted onto
the composition root.

The approved set lives inside this gate as :data:`APPROVED_SLOTS`. Adding
a slot is a deliberate decision: the contributor edits this gate, which
makes the change visible in the diff and forces alignment with the
feature-manifest substrate. Removal of a slot is also caught (an attribute
disappearing from ``AppState`` without updating the gate means the gate is
stale and must be refreshed in the same PR).

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

APPROVED_SLOTS: Final[frozenset[str]] = frozenset(
    {
        "_api_bridge_config",
        "_api_bridge_config_lock",
        "_auth_revalidate_max_failures",
        "_auth_revalidate_window_seconds",
        "_bridge_config_applied",
        "_brownfield_background_tasks",
        "_memory_bridge_config",
        "_memory_bridge_config_lock",
        "_objective_background_tasks",
        "_per_op_concurrency_config",
        "_per_op_rate_limit_config",
        "_request_lock_refs",
        "_request_locks",
        "_request_locks_guard",
        "_shutdown_requested",
        "_workers_bridge_config",
        "_workers_bridge_config_lock",
        "_ws_auth_timeout_seconds",
        "_ws_frame_timeout_seconds",
        "clock",
        "config",
        "startup_time",
    }
)
"""Cross-cutting mutable primitives that AppState may carry directly.

Every other piece of application state belongs in a feature state slice
(``BaseFeatureStateSlice``). This set is the contract the gate enforces.
"""


def extract_slots(state_py: Path) -> frozenset[str]:
    """Extract the ``__slots__`` declared on :class:`AppState`.

    AST-only parse; returns the empty frozenset when *state_py* has no
    ``AppState`` class or its ``__slots__`` is missing / not a literal
    tuple of strings.

    Args:
        state_py: Path to the module declaring ``AppState``.

    Returns:
        Set of slot names AppState declares.
    """
    try:
        tree = ast.parse(state_py.read_text(encoding="utf-8"), filename=str(state_py))
    except OSError, SyntaxError, UnicodeDecodeError:
        return frozenset()
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
            "AppState.__slots__ grew with un-approved attributes "
            f"({sorted(added)}). Move them into a feature state slice "
            "(BaseFeatureStateSlice subclass) OR update APPROVED_SLOTS in "
            "scripts/check_no_implicit_state_attribute.py with a rationale."
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
