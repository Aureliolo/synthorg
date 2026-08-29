#!/usr/bin/env python3
"""Pre-push / CI gate: the contract stage is unskippable and declares no dead knobs.

Two properties, each of which failed silently in the shape this stage replaces.

**A plan cannot reach EXECUTING except through SKELETON.** The whole value of a
contract written before the work is that every unit builds against it, and an
edge that steps over the stage costs exactly that: the units dispatch against
prose again and the first thing to reconcile their readings is the assembly at
the very end. Checked as a graph property rather than by naming the one edge
that used to exist, because a status added later with its own path to EXECUTING
would re-open the hole while every literal check still passed. The walk removes
SKELETON and asserts EXECUTING is then unreachable from APPROVED.

**Every gate the manifest declares is a gate something requires evidence of.**
The gate configuration is a definition of done, and a definition of done nobody
enforces is not one: a field an operator fills in that no requirement reads is
a project stating how it lints under a badge no linter ever earned. The rule is
that a ``*_command`` field on ``EnvironmentManifest`` appears in
``declared_gates``, which is what the oracle derives its requirements from, so a
field added later without extending the derivation fails here rather than
becoming a knob an operator sets in vain.

Both owners are checked for existence too, and their loss is exit 2 rather than
a violation: a transition table that stopped declaring these statuses, or a
manifest that stopped deriving its gates, reads exactly like a tree with nothing
to find, and a gate cannot report honestly once the thing it inspects is gone.

There is deliberately no per-line opt-out. An exception to either rule is a plan
that dispatches against no contract, or a knob nothing reads.

Usage::

    python scripts/check_skeleton_stage_paths.py
    python scripts/check_skeleton_stage_paths.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from collections import deque
from pathlib import Path
from typing import Final

_TRANSITIONS_REL: Final[str] = "src/synthorg/core/plan_transitions.py"
_MANIFEST_REL: Final[str] = "src/synthorg/engine/workspace/environment/manifest.py"

_STATUS_ENUM: Final[str] = "PlanStatus"
_APPROVED: Final[str] = "APPROVED"
_SKELETON: Final[str] = "SKELETON"
_EXECUTING: Final[str] = "EXECUTING"

_MANIFEST_MODEL: Final[str] = "EnvironmentManifest"
_GATES_PROPERTY: Final[str] = "declared_gates"
_COMMAND_SUFFIX: Final[str] = "_command"

#: The one command field that is not a gate: it is what PRODUCES the evidence
#: every other gate is judged beside, and the oracle reads its records directly
#: rather than through the derived map.
_NOT_A_GATE: Final[frozenset[str]] = frozenset({"test_command"})


def _parse(path: Path) -> ast.Module | None:
    """Parse a module, or return ``None`` when it cannot be read.

    Returns:
        The parsed tree, or ``None``.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError, SyntaxError:
        return None


def _status_name(node: ast.AST) -> str | None:
    """Read ``PlanStatus.<MEMBER>`` off *node*.

    Returns:
        The member name, or ``None`` when *node* is not a status reference.
    """
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == _STATUS_ENUM
    ):
        return node.attr
    return None


def _transition_graph(tree: ast.Module) -> dict[str, frozenset[str]]:
    """Read the declared status graph out of the transition table.

    The table is a dict literal keyed by status, whose values wrap a set literal
    of statuses. Read structurally rather than by importing, so the gate holds
    even for a tree that will not import.

    Returns:
        ``{source: targets}`` for every status the table declares.
    """
    graph: dict[str, frozenset[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            source = _status_name(key) if key is not None else None
            if source is None:
                continue
            graph[source] = frozenset(_targets(value))
    return graph


def _targets(value: ast.expr) -> set[str]:
    """Collect every status named anywhere inside a table entry's value.

    Walked rather than pattern-matched on ``frozenset({...})``, because the
    entries are written both ways and the question is only which statuses the
    entry admits.

    Returns:
        The target status names.
    """
    return {
        name for node in ast.walk(value) if (name := _status_name(node)) is not None
    }


def _reaches(
    graph: dict[str, frozenset[str]], start: str, goal: str, *, without: str
) -> bool:
    """Whether *goal* is reachable from *start* without passing through *without*.

    Returns:
        ``True`` when a path exists that never enters *without*.
    """
    seen = {start, without}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for target in graph.get(current, frozenset()):
            if target == goal:
                return True
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return False


def _command_fields(tree: ast.Module) -> set[str]:
    """Every ``*_command`` field declared on the manifest model.

    Returns:
        The field names, empty when the model was not found.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == _MANIFEST_MODEL:
            return {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id.endswith(_COMMAND_SUFFIX)
            }
    return set()


def _gate_fields(tree: ast.Module) -> set[str] | None:
    """Every manifest field the ``declared_gates`` property reads.

    Returns:
        The field names read as ``self.<field>``, or ``None`` when the property
        is absent, which the caller reports as a configuration error.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != _GATES_PROPERTY:
            continue
        return {
            read.attr
            for read in ast.walk(node)
            if isinstance(read, ast.Attribute)
            and isinstance(read.value, ast.Name)
            and read.value.id == "self"
        }
    return None


def scan_repo(repo_root: Path) -> tuple[str, ...]:
    """Check both properties.

    Args:
        repo_root: Repository root to scan.

    Returns:
        The violations, each a ready-to-print line.

    Raises:
        ValueError: When an owner cannot be read, or has stopped declaring the
            thing this gate inspects.
    """
    transitions = _parse(repo_root / _TRANSITIONS_REL)
    if transitions is None:
        msg = f"cannot read {_TRANSITIONS_REL}"
        raise ValueError(msg)
    graph = _transition_graph(transitions)
    for required in (_APPROVED, _SKELETON, _EXECUTING):
        if required not in graph:
            msg = (
                f"{_TRANSITIONS_REL}: declares no {_STATUS_ENUM}.{required} entry,"
                " so the contract stage cannot be checked at all"
            )
            raise ValueError(msg)

    manifest = _parse(repo_root / _MANIFEST_REL)
    if manifest is None:
        msg = f"cannot read {_MANIFEST_REL}"
        raise ValueError(msg)
    read_by_gates = _gate_fields(manifest)
    if read_by_gates is None:
        msg = (
            f"{_MANIFEST_REL}: {_MANIFEST_MODEL} declares no {_GATES_PROPERTY}"
            " property, so nothing derives what the oracle requires evidence of"
        )
        raise ValueError(msg)

    return (*_graph_violations(graph), *_gate_field_violations(manifest, read_by_gates))


def _graph_violations(graph: dict[str, frozenset[str]]) -> tuple[str, ...]:
    """Check the contract stage is both unskippable and passable.

    Returns:
        One message per way the stage stops being a gate.
    """
    violations: list[str] = []
    if _EXECUTING in graph[_APPROVED]:
        violations.append(
            f"{_TRANSITIONS_REL}: {_STATUS_ENUM}.{_APPROVED} reaches"
            f" {_STATUS_ENUM}.{_EXECUTING} directly, so a plan can dispatch its"
            " units against a contract that was never written"
        )
    elif _reaches(graph, _APPROVED, _EXECUTING, without=_SKELETON):
        violations.append(
            f"{_TRANSITIONS_REL}: {_STATUS_ENUM}.{_APPROVED} reaches"
            f" {_STATUS_ENUM}.{_EXECUTING} without passing through"
            f" {_STATUS_ENUM}.{_SKELETON}, so the contract stage is skippable"
        )
    if _EXECUTING not in graph[_SKELETON]:
        violations.append(
            f"{_TRANSITIONS_REL}: {_STATUS_ENUM}.{_SKELETON} cannot reach"
            f" {_STATUS_ENUM}.{_EXECUTING}, so a passing contract strands its plan"
        )
    return tuple(violations)


def _gate_field_violations(
    manifest: ast.Module, read_by_gates: set[str]
) -> tuple[str, ...]:
    """Check every declared gate command is one the oracle requires.

    Returns:
        One message per command field no gate map reads.
    """
    return tuple(
        f"{_MANIFEST_REL}: {_MANIFEST_MODEL}.{field} is declared and"
        f" {_GATES_PROPERTY} does not read it, so a project can declare it"
        " and nothing will ever require evidence that it ran"
        for field in sorted(_command_fields(manifest) - _NOT_A_GATE - read_by_gates)
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when the contract stage is unskippable and every gate is read.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    repo_root = (
        args.repo_root.resolve()
        if args.repo_root is not None
        else Path(__file__).resolve().parent.parent
    )

    try:
        violations = scan_repo(repo_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for violation in violations:
        print(violation)
    if violations:
        print(
            f"\n{len(violations)} skeleton-stage violation(s). A plan must reach"
            " EXECUTING only through SKELETON, and every declared gate command"
            " must be one the oracle requires evidence of.",
            file=sys.stderr,
        )
        return 1
    print("OK: the contract stage is unskippable and every declared gate is read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
