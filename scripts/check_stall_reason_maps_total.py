#!/usr/bin/env python3
"""Pre-push / CI gate: every stall reason is answered by the maps that index it.

``StallReason`` says why an initiative can no longer advance on its own, and
three declarations are keyed by it. Two of them are read with ``[]``, so a
member missing from either does not degrade: it raises.

Where that lands is what makes it worth a gate. The raise happens inside a
detached rollup task, where it is swallowed to a warning, so the initiative does
not fail loudly. It simply never replans and never escalates, the recovery sweep
re-drives it on every cadence, and the plan sits stalled for ever while the
board shows work in flight. ``SKELETON_FAILED`` shipped in exactly that state:
fired by the rollup, absent from both maps, and invisible to every other gate
and to 43,000 passing tests, because nothing anywhere asks this question.

Three properties, over the three declarations:

**Guidance is total.** ``_REASON_GUIDANCE`` is what the replan brief tells the
successor's planner, and it is indexed by the reason directly. A missing member
is a replan that raises instead of being planned.

**The two stall families partition the enum.** ``ITEM_DERIVED_STALLS`` names the
reasons re-confirmed by re-reading the items; ``STAGE_OF_STALL_REASON`` names
the reasons re-confirmed by re-reading the stage they came from, and maps each
to that stage. A member in neither is a stall nothing can re-confirm; a member
in both is two answers to which half re-confirms it. The partition IS the
invariant, so it is checked as one rather than as two independent lists.

**Every declaration must be found.** Losing one is exit 2 rather than a pass: a
declaration this gate cannot locate reads exactly like an enum with nothing
missing, and a gate that has gone blind must say so rather than report success.

No baseline, and deliberately no per-line opt-out. An exception here is a stall
reason with no way forward, which is the deadlock the rule exists to refuse.

Usage::

    python scripts/check_stall_reason_maps_total.py
    python scripts/check_stall_reason_maps_total.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Final

_COMPLETION_REL: Final[str] = "src/synthorg/engine/initiative/completion.py"
_BRIEF_REL: Final[str] = "src/synthorg/engine/initiative/replan_brief.py"

_ENUM: Final[str] = "StallReason"
_GUIDANCE: Final[str] = "_REASON_GUIDANCE"
_ITEM_DERIVED: Final[str] = "ITEM_DERIVED_STALLS"
_STAGE_OF: Final[str] = "STAGE_OF_STALL_REASON"


def _parse(repo_root: Path, relative: str) -> ast.Module:
    """Parse one tracked module.

    Returns:
        The module's AST.

    Raises:
        ValueError: When the file is missing or will not parse.
    """
    path = repo_root / relative
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read {relative}: {exc}"
        raise ValueError(msg) from exc
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        msg = f"cannot parse {relative}: {exc}"
        raise ValueError(msg) from exc


def _enum_members(tree: ast.Module, relative: str) -> frozenset[str]:
    """Collect every member declared on the stall-reason enum.

    Returns:
        The member names.

    Raises:
        ValueError: When the enum is absent or declares nothing.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != _ENUM:
            continue
        members = frozenset(
            target.id
            for statement in node.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        )
        if not members:
            msg = f"{relative}: {_ENUM} declares no members"
            raise ValueError(msg)
        return members
    msg = f"{relative}: declares no {_ENUM}"
    raise ValueError(msg)


def _keys_named(node: ast.AST) -> frozenset[str]:
    """Collect every ``StallReason.X`` attribute reached from *node*.

    Read as attribute accesses anywhere inside the declaration rather than as
    dict keys specifically, so the set literal and the two mappings are all
    answered by one reader and a declaration that changes container type does
    not silently stop being checked.

    Returns:
        The member names named in the declaration.
    """
    return frozenset(
        child.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id == _ENUM
    )


def _declaration(tree: ast.Module, name: str, relative: str) -> frozenset[str]:
    """Read the stall reasons one module-level declaration names.

    Returns:
        The member names it names.

    Raises:
        ValueError: When the declaration is absent or names no member.
    """
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        named = _keys_named(node)
        if not named:
            msg = f"{relative}: {name} names no {_ENUM} member"
            raise ValueError(msg)
        return named
    msg = f"{relative}: declares no {name}"
    raise ValueError(msg)


def scan_repo(repo_root: Path) -> tuple[str, ...]:
    """Check every stall reason is answered by the maps that index it.

    Returns:
        One message per unanswered or doubly-answered reason.

    Raises:
        ValueError: When a declaration this gate reads cannot be found.
    """
    completion = _parse(repo_root, _COMPLETION_REL)
    brief = _parse(repo_root, _BRIEF_REL)

    members = _enum_members(completion, _COMPLETION_REL)
    guidance = _declaration(brief, _GUIDANCE, _BRIEF_REL)
    item_derived = _declaration(completion, _ITEM_DERIVED, _COMPLETION_REL)
    stage_of = _declaration(completion, _STAGE_OF, _COMPLETION_REL)

    violations: list[str] = []
    violations.extend(
        f"{_BRIEF_REL}: {_ENUM}.{member} has no {_GUIDANCE} entry, so a replan"
        " on that reason raises instead of being planned."
        for member in sorted(members - guidance)
    )
    violations.extend(
        f"{_COMPLETION_REL}: {_ENUM}.{member} is in neither {_ITEM_DERIVED} nor"
        f" {_STAGE_OF}, so nothing can re-confirm a stall on that reason and"
        " the initiative parks with no exit."
        for member in sorted(members - (item_derived | stage_of))
    )
    violations.extend(
        f"{_COMPLETION_REL}: {_ENUM}.{member} is in both {_ITEM_DERIVED} and"
        f" {_STAGE_OF}, which is two answers to how that stall is re-confirmed."
        for member in sorted(item_derived & stage_of)
    )
    violations.extend(
        f"{_ENUM}.{member} is named by a declaration but is not a member of"
        " the enum, so the entry answers a reason nothing can produce."
        for member in sorted((guidance | item_derived | stage_of) - members)
    )
    return tuple(violations)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when every stall reason is answered exactly once.
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
            f"\n{len(violations)} stall-reason violation(s). Every StallReason must"
            " carry replan guidance and belong to exactly one re-confirmation"
            " family, or the initiative it describes parks with no way forward.",
            file=sys.stderr,
        )
        return 1
    print("OK: every stall reason carries guidance and one re-confirmation family.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
