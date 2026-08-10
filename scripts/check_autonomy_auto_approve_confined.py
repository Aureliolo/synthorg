#!/usr/bin/env python3
"""Pre-push / CI gate: a preset never auto-approves what leaves the worktree.

An autonomy preset grants bare categories (``"code"``, ``"docs"``), and
``AutonomyResolver`` expands them at resolve time. So the auto-approved set is
not a list anybody wrote; it is whatever the taxonomy happens to contain. Add
one ``ActionType`` member under an already-granted prefix and every SUPERVISED
agent gains it, with no decision and nothing in the diff that looks like a
security change.

That is not hypothetical. ``ToolCategory.DESIGN`` defaulted to ``docs:write``,
so auto-approving ``"docs"`` also auto-approved an image generator that calls a
billed external provider and an asset manager that deletes stored assets, under
a type the risk map scores LOW. The preset's own description said anything
leaving the sandbox needs a human. It did not.

The gate holds the boundary the description claims: every concrete type a
built-in preset auto-approves must first be declared in
``security.action_types.WORKTREE_CONFINED_ACTION_TYPES``. Adding a member is
still allowed; adding it *silently* is not. The FULL preset is exempt by
inspection rather than by omission: its grant is the literal ``"all"``, which
means everything and says so in its description.

Membership is a claim about where an action lands, not about how dangerous the
verb sounds, so the gate deliberately does NOT cross-check the risk map.
``code:delete`` is HIGH there and confined here, and both are right: deleting a
file inside a directory nobody keeps is not the same act as deleting one that
outlives the run.

There is deliberately no baseline and no per-line opt-out. An exception is a
line added to the declaration, in the open, next to the sentence that says what
membership means.

Usage::

    python scripts/check_autonomy_auto_approve_confined.py
    python scripts/check_autonomy_auto_approve_confined.py --repo-root /path
"""

import argparse
import importlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from synthorg.security.action_types import (
    WORKTREE_CONFINED_ACTION_TYPES,
    ActionTypeRegistry,
)
from synthorg.security.autonomy.models import BUILTIN_PRESETS, AutonomyPreset

_PACKAGE = "synthorg.security"

#: Where the package lives relative to a repository root, for the check that
#: ``--repo-root`` names the checkout the presets were actually imported from.
_SRC_DIR = "src"

#: An explicit grant of everything, which is what the FULL preset means and
#: what its description promises. Confinement is not a claim it makes.
_GRANT_ALL = "all"


@dataclass(frozen=True, slots=True)
class Violation:
    """An auto-approved action type nobody declared as worktree-confined.

    Attributes:
        level: The autonomy level whose preset grants it.
        pattern: The preset entry that expanded to it.
        action_type: The concrete action type that is not declared.
    """

    level: str
    pattern: str
    action_type: str


def _expand(registry: ActionTypeRegistry, pattern: str) -> frozenset[str]:
    """Expand one preset entry the way the resolver does.

    Built-ins only, matching ``AutonomyResolver._expand_patterns``: a custom
    type an operator registers later is not admitted by a bare category, so
    the gate has the whole auto-approved set in front of it.

    Args:
        registry: Registry answering the taxonomy.
        pattern: A preset entry: a category prefix or a concrete type.

    Returns:
        The concrete action types *pattern* grants.
    """
    category_types = registry.expand_category(pattern, builtin_only=True)
    return category_types or frozenset({pattern})


def scan_presets(
    presets: Mapping[str, AutonomyPreset] | None = None,
    *,
    confined: frozenset[str] | None = None,
) -> list[Violation]:
    """Find every auto-approved type missing from the confinement declaration.

    Args:
        presets: Presets to scan. ``None`` scans the shipped built-ins,
            which is what the gate run does.
        confined: The confinement declaration to check against. ``None``
            uses the shipped one.

    Returns:
        One violation per (level, pattern, action type), sorted.
    """
    presets = BUILTIN_PRESETS if presets is None else presets
    declared = WORKTREE_CONFINED_ACTION_TYPES if confined is None else confined
    registry = ActionTypeRegistry()
    violations: list[Violation] = []
    for level, preset in presets.items():
        for pattern in preset.auto_approve:
            if pattern == _GRANT_ALL:
                continue
            violations.extend(
                Violation(
                    level=str(level),
                    pattern=pattern,
                    action_type=action_type,
                )
                for action_type in sorted(_expand(registry, pattern) - declared)
            )
    return sorted(violations, key=lambda v: (v.level, v.pattern, v.action_type))


def _describe_root_mismatch(repo_root: Path | None) -> str | None:
    """Return why *repo_root* is not the checkout the presets came from.

    Args:
        repo_root: The root the caller claims to be checking.

    Returns:
        A message, or ``None`` when the two agree or none was supplied.
    """
    if repo_root is None:
        return None
    package = importlib.import_module(_PACKAGE)
    imported = Path(next(iter(package.__path__))).resolve()
    expected = (repo_root / _SRC_DIR / _PACKAGE.replace(".", "/")).resolve()
    if imported == expected:
        return None
    return (
        f"--repo-root points at {repo_root}, but {_PACKAGE} was imported from"
        f" {imported}. The presets are read from the imported package, so this"
        " run would report on a different checkout."
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when every auto-approved action type is declared confined.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    if (mismatch := _describe_root_mismatch(args.repo_root)) is not None:
        print(mismatch, file=sys.stderr)
        return 2

    violations = scan_presets()
    for violation in violations:
        print(
            f"{violation.level}: auto_approve {violation.pattern!r} expands to"
            f" {violation.action_type!r}, which is not declared in"
            " WORKTREE_CONFINED_ACTION_TYPES. Either its effect stays inside"
            " the agent's own worktree, and it is declared there, or it needs"
            " a human and belongs outside the granted category."
        )
    if violations:
        print(
            f"\n{len(violations)} auto-approved action type(s) undeclared."
            " A preset grants categories, so a new member joins the"
            " auto-approved set on its own; declaring it is how that becomes"
            " a decision.",
            file=sys.stderr,
        )
        return 1
    print("OK: every auto-approved action type is declared worktree-confined.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
