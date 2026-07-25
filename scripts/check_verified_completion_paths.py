#!/usr/bin/env python3
"""Pre-push / CI gate: an initiative completes only through the verified path.

The general loop's forcing property is that "done" is never a claim anyone can
make directly. Three structures hold it up, and each is one careless edit away
from being undone, so each is checked here.

1. **The tail is unskippable.** ``PlanStatus.EXECUTING`` must not reach
   ``COMPLETED``, and ``ProjectStatus.ACTIVE`` must not either: delivery is
   reachable only from the evaluate stage. Re-adding either edge would restore
   the old behaviour where a pile of individually-verified pieces completed an
   initiative nobody had assembled or scored.

2. **Only the evaluate stage completes a plan.** A call writing
   ``PlanStatus.COMPLETED`` through the audited plan-status seam belongs to
   :mod:`synthorg.engine.initiative.evaluate` and nowhere else. Any other
   writer is a second delivery path that skips the verdict. The owner is
   excluded by filename, so this does not distinguish call sites *within* that
   module; the companion check is that ``derive_plan_status`` never returns
   ``COMPLETED``, so a status the rollup computed can never reach the seam
   either.

3. **Every work unit declares a deliverable.** ``PlanItem`` and
   ``DecompositionPlan`` must both call ``validate_expected_artifacts``: it is
   what arms the fail-loud zero-artifact guard on the dispatched task, so
   dropping it silently re-opens the "chat-only run looks finished" hole.

Sanctioned exceptions opt out with a per-line trailing comment::

    await writer.sync_status(
        plan, PlanStatus.COMPLETED
    )  # lint-allow: verified-completion -- <reason>

The justification after ``--`` is required. There is no baseline file: the rule
ships with zero offenders.

Usage::

    python scripts/check_verified_completion_paths.py
    python scripts/check_verified_completion_paths.py --repo-root PATH
"""

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Final

SUPPRESSION_MARKER: Final[str] = "lint-allow: verified-completion"

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*verified-completion\s*--\s*\S",
)

#: The only module allowed to write a plan's COMPLETED status. Delivery is its
#: verdict; every other writer would be a way around that verdict.
_PLAN_COMPLETION_OWNER: Final[str] = "src/synthorg/engine/initiative/evaluate.py"

#: Seams that persist a plan-status transition.
_PLAN_STATUS_SEAMS: Final[frozenset[str]] = frozenset({"sync_status", "_advance_plan"})

#: Files the invariant checks read, relative to the repo root.
_PLAN_TRANSITIONS: Final[str] = "src/synthorg/core/plan_transitions.py"
_PROJECT_TRANSITIONS: Final[str] = "src/synthorg/core/project_transitions.py"
_ARTIFACT_VALIDATORS: Final[tuple[str, ...]] = (
    "src/synthorg/core/plan.py",
    "src/synthorg/engine/decomposition/models.py",
)
_ARTIFACT_VALIDATOR_CALL: Final[str] = "validate_expected_artifacts"

#: The forbidden edges, as ``(source, forbidden target)`` per machine.
_FORBIDDEN_EDGES: Final[tuple[tuple[str, str, str], ...]] = (
    (_PLAN_TRANSITIONS, "EXECUTING", "COMPLETED"),
    (_PROJECT_TRANSITIONS, "ACTIVE", "COMPLETED"),
)

#: Where COMPLETED is legitimately reachable from, per machine.
_COMPLETION_SOURCE: Final[tuple[tuple[str, str], ...]] = (
    (_PLAN_TRANSITIONS, "EVALUATING"),
    (_PROJECT_TRANSITIONS, "EVALUATING"),
)


def _read(root: Path, rel: str) -> tuple[str, ast.Module] | None:
    """Read and parse a repo-relative source file.

    Returns:
        The source text and its parsed tree, or ``None`` when unreadable.
    """
    path = root / rel
    try:
        source = path.read_text(encoding="utf-8")
        return source, ast.parse(source)
    except OSError, SyntaxError:
        return None


def _transition_map(tree: ast.Module) -> dict[str, set[str]]:
    """Extract ``VALID_TRANSITIONS`` as ``{source: {target, ...}}``.

    Reads the literal dict statically, so the gate cannot be fooled by a
    machine assembled at runtime: an unreadable table yields an empty map and
    the reachability check below reports it.

    Returns:
        The declared edges, keyed by source-status member name.
    """
    edges: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            named = any(
                isinstance(t, ast.Name) and t.id == "VALID_TRANSITIONS"
                for t in node.targets
            )
        elif isinstance(node, ast.AnnAssign):
            named = (
                isinstance(node.target, ast.Name)
                and node.target.id == "VALID_TRANSITIONS"
            )
        else:
            continue
        if not named or not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if not isinstance(key, ast.Attribute):
                continue
            edges[key.attr] = {
                member.attr
                for member in ast.walk(value)
                if isinstance(member, ast.Attribute)
            }
    return edges


def _check_state_machines(root: Path) -> list[str]:
    """Check that delivery is reachable only from the evaluate stage.

    Returns:
        One message per violated invariant.
    """
    messages: list[str] = []
    for rel, source_status, forbidden in _FORBIDDEN_EDGES:
        parsed = _read(root, rel)
        if parsed is None:
            messages.append(f"{rel}: unreadable; the forcing invariant is unchecked")
            continue
        edges = _transition_map(parsed[1])
        if forbidden in edges.get(source_status, set()):
            messages.append(
                f"{rel}: {source_status} -> {forbidden} is back. The tail "
                "(integrate, then evaluate) is what makes delivery mean "
                "something; a direct edge lets a plan complete without being "
                "assembled or scored."
            )
    for rel, expected_source in _COMPLETION_SOURCE:
        parsed = _read(root, rel)
        if parsed is None:
            continue
        edges = _transition_map(parsed[1])
        sources = {src for src, targets in edges.items() if "COMPLETED" in targets}
        if sources != {expected_source}:
            messages.append(
                f"{rel}: COMPLETED is reachable from {sorted(sources)}, expected "
                f"only from {expected_source}. Delivery has exactly one "
                "predecessor by design."
            )
    return messages


def _check_derivation_never_completes(root: Path) -> list[str]:
    """Check that ``derive_plan_status`` cannot return COMPLETED.

    The writer check above matches a literal ``PlanStatus.COMPLETED`` argument,
    so it cannot see ``_advance_plan(plan, derived)``. That call is safe only
    because the derivation has no COMPLETED branch; making that explicit here
    keeps the two halves of the invariant from drifting apart.

    Returns:
        One message when the derivation gained a COMPLETED branch.
    """
    rel = "src/synthorg/engine/initiative/completion.py"
    parsed = _read(root, rel)
    if parsed is None:
        return [f"{rel}: unreadable; the derivation invariant is unchecked"]
    seen = False
    for node in ast.walk(parsed[1]):
        if (
            not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            or node.name != "derive_plan_status"
        ):
            continue
        seen = True
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr == "COMPLETED"
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "PlanStatus"
            ):
                return [
                    (
                        f"{rel}:{inner.lineno}: derive_plan_status names "
                        "PlanStatus.COMPLETED. The rollup writes whatever this "
                        "derives, so a COMPLETED branch here is a second delivery "
                        "path that skips the evaluate stage's verdict."
                    )
                ]
    if not seen:
        return [
            (
                f"{rel}: derive_plan_status not found; the derivation invariant is "
                "unchecked. Point the gate at its new home rather than leaving it "
                "silently satisfied."
            )
        ]
    return []


def _check_plan_completion_writers(root: Path) -> list[str]:
    """Check that only the evaluate stage writes a plan's COMPLETED status.

    Returns:
        One message per unsanctioned writer.
    """
    messages: list[str] = []
    for path in sorted((root / "src" / "synthorg").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == _PLAN_COMPLETION_OWNER:
            continue
        parsed = _read(root, rel)
        if parsed is None:
            continue
        source, tree = parsed
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name not in _PLAN_STATUS_SEAMS:
                continue
            arguments = [*node.args, *(kw.value for kw in node.keywords)]
            if not any(
                isinstance(arg, ast.Attribute)
                and arg.attr == "COMPLETED"
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "PlanStatus"
                for arg in arguments
            ):
                continue
            end = node.end_lineno or node.lineno
            span = "\n".join(lines[node.lineno - 1 : min(end, len(lines))])
            if _SUPPRESSION_RE.search(span):
                continue
            messages.append(
                f"{rel}:{node.lineno}: writes PlanStatus.COMPLETED through "
                f"{name!r}. Delivery is the evaluate stage's verdict; another "
                "writer is a way around it. Move the write to "
                f"{_PLAN_COMPLETION_OWNER}, or add "
                f"'# {SUPPRESSION_MARKER} -- <reason>' on this line."
            )
    return messages


def _check_artifact_invariant(root: Path) -> list[str]:
    """Check that both plan-shaped models still enforce a declared deliverable.

    Returns:
        One message per model that stopped enforcing it.
    """
    messages: list[str] = []
    for rel in _ARTIFACT_VALIDATORS:
        parsed = _read(root, rel)
        if parsed is None:
            messages.append(
                f"{rel}: unreadable; the deliverable invariant is unchecked"
            )
            continue
        calls = {
            node.func.id
            for node in ast.walk(parsed[1])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if _ARTIFACT_VALIDATOR_CALL not in calls:
            messages.append(
                f"{rel}: no longer calls {_ARTIFACT_VALIDATOR_CALL}. A WORK unit "
                "with no declared deliverable disarms the fail-loud "
                "zero-artifact guard, so a run that produced nothing reads as "
                "finished."
            )
    return messages


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` when every invariant holds, ``1`` when one is violated, ``2`` on
        a bad ``--repo-root``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root to anchor path resolution against.",
    )
    args = parser.parse_args(argv)

    root = args.repo_root or Path(__file__).resolve().parent.parent
    if not root.is_dir():
        print(f"--repo-root must be a directory: {root}", file=sys.stderr)
        return 2

    messages = [
        *_check_state_machines(root),
        *_check_derivation_never_completes(root),
        *_check_plan_completion_writers(root),
        *_check_artifact_invariant(root),
    ]
    if messages:
        for message in messages:
            print(message)
        print(
            f"\n{len(messages)} verified-completion violation(s) found. "
            "See docs/design/initiative-tail.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
