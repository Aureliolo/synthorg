#!/usr/bin/env python3
"""Pre-push / CI gate: only the fallback stamps ``planning_strategy``.

``planning_strategy`` marks a SUBSTITUTION. Blank means the strategy the
operator configured produced the plan; a name means a fallback stood in, and
the approval gate and the dashboard show it so nobody approves a single-shot
substitute believing it is the researched plan they asked for.

That meaning survives only while exactly one path writes the field. A second
writer does not override the first (they sit on mutually exclusive paths), so
nothing collides and nothing is logged: what breaks is the READER. The
agent-session strategy once stamped its own name on its own success path, so
every plan carried a name, and a name on every plan carries exactly as much
information as a name on none. The dashboard row appeared on every plan, and
the recursion-depth sweep's substitution detector refused every valid tree it
was given.

Detection
---------
A **write** is a keyword argument named ``planning_strategy``, or that key in a
``model_copy(update=...)`` mapping. ``model_copy`` is included because it skips
validation: stamping the field on an already-built plan is what a second writer
looks like once it stops constructing one. The mapping is read as a dict
literal, as the ``dict(...)`` keyword form, and through a module-local or
function-local name assigned one, because building it a line above the call is
one refactor away from a literal-only check.

A **propagation** is not a write: a value read straight off another object's
``planning_strategy`` (``planning_strategy=result.plan.planning_strategy``)
carries the decision rather than taking it, which is how the durable ``Plan``
receives what the strategy decided. Only the expression's shape distinguishes
the two, and that is decidable, so it is what this gate reads.

Declared owner
--------------
One function, because one writer is the whole rule. It must still write the
field: a declaration that has outlived its site is an exemption the next writer
inherits silently.

What it does NOT do
-------------------
It says nothing about a construction whose keywords no static read can open
(``DecompositionPlan(**payload)``), and nothing about which NAME the owner
stamps. Both are outside what an AST can decide.

Allowlist / opt-out
-------------------
Deliberately none, and no baseline. A second writer is never something to
preserve: an exception means changing the declared owner, in the open.

Usage::

    uv run python scripts/check_single_planning_strategy_writer.py

Exit codes:
    0 -- only the owner writes the field.
    1 -- something outside the owner writes it.
    2 -- configuration error (bad ``--repo-root``, a stale owner declaration,
         or a source file that could not be read or parsed -- fail-closed).
"""

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCAN_ROOT_REL: Final[str] = "src/synthorg"

#: The field whose meaning depends on there being one writer.
_MARKER_FIELD: Final[str] = "planning_strategy"

_COPY_METHOD: Final[str] = "model_copy"

#: What bounds a namespace, and therefore how far a mapping name reaches.
_SCOPE_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
)

#: The one function that may stamp the field, and why. Written with the reason
#: because the reason is the whole exemption.
_OWNER_REL: Final[str] = "src/synthorg/engine/decomposition/agent_session.py"
_OWNER_FUNC: Final[str] = "_fallback_plan"
_OWNER_REASON: Final[str] = (
    "it is the substitution: it runs the single-shot planner in place of the "
    "researched session, which is the event the field exists to record"
)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One place outside the owner that stamps the marker."""

    rel: str
    lineno: int
    col: int
    kind: str

    def message(self) -> str:
        """Return the human-facing violation message.

        Returns:
            The formatted violation line.
        """
        what = (
            f"sets {_MARKER_FIELD}="
            if self.kind == "keyword"
            else f"stamps {_MARKER_FIELD} through {_COPY_METHOD}"
        )
        return (
            f"{self.rel}:{self.lineno}:{self.col}: {what}, which makes a second "
            f"writer of a field whose meaning needs exactly one. The field "
            f"marks a SUBSTITUTION, so a name written anywhere but the fallback "
            f"reads identically to the fallback having stood in, and a reader "
            f"cannot tell a researched plan from a substituted one. Leave it "
            f"blank, or propagate the value already on the source plan."
        )


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Returns:
        The resolved project-root directory.

    Raises:
        ProjectRootError: If *repo_root* cannot be resolved to an existing
            directory.
    """
    if repo_root is None:
        return _REPO_ROOT
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` under *abs_root* as ``(abs, rel)``.

    Falls back to :meth:`Path.rglob` when ``git`` is unavailable, warning on
    stderr because the fallback widens scope to untracked files.

    Returns:
        A list of ``(absolute_path, posix_relative_path)`` pairs.
    """
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", rel_root],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        print(
            f"check_single_planning_strategy_writer: git ls-files failed in "
            f"{project_root} ({type(exc).__name__}: {exc}); falling back to "
            f"rglob (scope widens to include untracked / gitignored files).",
            file=sys.stderr,
        )
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p and p.endswith(".py")]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _is_propagation(value: ast.expr) -> bool:
    """Return whether *value* carries the marker rather than deciding it.

    Reading the field off another object hands on a decision somebody else
    took, which is how the durable plan receives what the strategy chose. The
    expression's shape is the whole difference, and it is the part an AST can
    settle.

    Returns:
        ``True`` when the value is an attribute read of the same field.
    """
    return isinstance(value, ast.Attribute) and value.attr == _MARKER_FIELD


def _mapping_writes(node: ast.expr) -> bool:
    """Return whether an update mapping stamps the marker.

    Reads a dict literal and the ``dict(planning_strategy=...)`` keyword form,
    which are the same mapping spelled two ways. A propagated value is not a
    stamp, in either spelling.

    Returns:
        ``True`` when the mapping sets the field to a decided value.
    """
    if isinstance(node, ast.Dict):
        return any(
            isinstance(key, ast.Constant)
            and key.value == _MARKER_FIELD
            and not _is_propagation(value)
            for key, value in zip(node.keys, node.values, strict=True)
            if key is not None
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
    ):
        return any(
            kw.arg == _MARKER_FIELD and not _is_propagation(kw.value)
            for kw in node.keywords
        )
    return False


def _nodes_in_scope(scope: ast.AST) -> Iterator[ast.AST]:
    """Walk *scope* down to, but not into, the namespaces nested inside it.

    Yields:
        Every node belonging to *scope*'s own namespace.
    """
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        current = stack.pop()
        yield current
        if not isinstance(current, _SCOPE_NODES):
            stack.extend(ast.iter_child_nodes(current))


def _stamping_names(scope: ast.AST) -> frozenset[str]:
    """Return the names in *scope* bound to a mapping that stamps the marker.

    One namespace at a time, because a name is not a module-wide fact: a
    helper building its own ``updates`` must not authorise an unrelated
    function's.

    Returns:
        The names whose mapping stamps the field.
    """
    names: set[str] = set()
    for node in _nodes_in_scope(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _mapping_writes(node.value):
                    names.add(target.id)
                elif (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == _MARKER_FIELD
                    and not _is_propagation(node.value)
                ):
                    names.add(target.value.id)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
            and _mapping_writes(node.value)
        ):
            names.add(node.target.id)
    return frozenset(names)


def _copy_stamps(node: ast.Call, named: frozenset[str]) -> bool:
    """Return whether *node* is a ``model_copy`` that stamps the marker.

    Returns:
        ``True`` when the call rewrites the field.
    """
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == _COPY_METHOD):
        return False
    for keyword in node.keywords:
        if keyword.arg != "update":
            continue
        if isinstance(keyword.value, ast.Name):
            return keyword.value.id in named
        return _mapping_writes(keyword.value)
    return False


def _site_kind(node: ast.Call, named: frozenset[str]) -> str | None:
    """Classify *node* as a stamping site, or ``None`` when it is not one.

    Returns:
        ``"keyword"``, ``"copy"``, or ``None``.
    """
    if any(
        kw.arg == _MARKER_FIELD and not _is_propagation(kw.value)
        for kw in node.keywords
    ):
        return "keyword"
    return "copy" if _copy_stamps(node, named) else None


@dataclass(frozen=True)
class _Site:
    """One place the marker is stamped, and the function it sits in."""

    lineno: int
    col: int
    kind: str
    func: str | None


def _stamping_sites(tree: ast.Module) -> list[_Site]:
    """Return every call in *tree* that stamps the marker.

    Only the OUTERMOST stamping call in a nest is reported:
    ``model_copy(update=dict(planning_strategy=name))`` is one decision written
    two levels deep, and counting it twice would report one violation as two.

    Args:
        tree: The parsed module.

    Returns:
        One entry per site.
    """
    sites: list[_Site] = []

    def _visit(node: ast.AST, visible: frozenset[str], func: str | None) -> None:
        """Walk *node*, tracking the innermost enclosing function name."""
        if isinstance(node, ast.Call):
            kind = _site_kind(node, visible)
            if kind is not None:
                sites.append(
                    _Site(lineno=node.lineno, col=node.col_offset, kind=kind, func=func)
                )
                return
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, _SCOPE_NODES):
                _visit(child, visible, func)
                continue
            inner = (
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else func
            )
            _visit(child, visible | _stamping_names(child), inner)

    _visit(tree, _stamping_names(tree), None)
    return sites


def _scan_all(project_root: Path) -> tuple[list[_Hit], int]:
    """Scan ``src/synthorg`` for the marker being stamped outside the owner.

    Returns:
        ``(hits, owner_site_count)``.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    hits: list[_Hit] = []
    owner_sites = 0
    for path, rel in _git_tracked_python_files(abs_root, project_root):
        _, tree = read_and_parse(path)
        for site in _stamping_sites(tree):
            if rel == _OWNER_REL and site.func == _OWNER_FUNC:
                owner_sites += 1
                continue
            hits.append(_Hit(rel=rel, lineno=site.lineno, col=site.col, kind=site.kind))
    return hits, owner_sites


def _iter_hits(hits: list[_Hit]) -> Iterator[str]:
    """Yield each violation message in a stable order.

    Yields:
        One formatted violation line per hit.
    """
    for hit in sorted(hits, key=lambda h: (h.rel, h.lineno, h.col)):
        yield hit.message()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The gate exit code (0 clean, 1 violation, 2 configuration error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to this script's repo).",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        hits, owner_sites = _scan_all(project_root)
    except GateSourceError as exc:
        print(f"check_single_planning_strategy_writer: {exc}", file=sys.stderr)
        return 2

    if not owner_sites:
        print(
            f"check_single_planning_strategy_writer: {_OWNER_REL}::{_OWNER_FUNC} is "
            f"declared the one writer of {_MARKER_FIELD} ({_OWNER_REASON}) but "
            f"stamps nothing. Point the declaration at the function that took the "
            f"substitution over: an unused exemption is one the next writer "
            f"inherits silently.",
            file=sys.stderr,
        )
        return 2

    if not hits:
        return 0
    for message in _iter_hits(hits):
        print(message)
    print(
        f"\n{len(hits)} site(s) stamp {_MARKER_FIELD} outside "
        f"{_OWNER_REL}::{_OWNER_FUNC}. The field records that a substitute "
        f"planner stood in; one writer is what keeps blank meaning "
        f"'the configured strategy produced this'.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
