#!/usr/bin/env python3
"""Pre-push / CI gate: one owner decides which roles may own a plan item.

A gate role (``Completion Reviewer``, ``Red Team``) is held by ordinary roster
agents, so it is staffed and it appears in every roster read. It JUDGES work
rather than performing it, so it is not something a plan item can be owned by.
Any rule that derives the planner's role list without asking what a role
confers therefore offers a judge as an executor, and a planner offered one
takes it: a live run put 19 of 102 subtasks under ``Completion Reviewer``,
seven of them atomic and due to execute.

Two things go wrong at once. The party that judges becomes the author of what
it judges, which the row-level ``CHECK (executor_agent_id <> reviewer_agent_id)``
does not cover because it constrains a verdict row rather than a plan. And an
experiment contrasting gated against ungated merges gets plan-level
verification in BOTH arms, so the contrast is contaminated at source.

``engine/decomposition/context.py::roster_from_agents`` is the single owner of
"which roles may own a plan item", and it is the one place the exclusion can
live: every consumer downstream of it (the ``required_role`` schema enum, the
``_roster_guidance`` system prompt line, ``describe_unroutable_role``) is a
pure function over the roster it is handed.

Detection
---------
Two checks over ``src/synthorg/`` and ``evals/``:

1. **The owner is intact.** ``roster_from_agents`` still exists in
   ``engine/decomposition/context.py`` and still calls ``role_is_gate_role``.
   Losing either is the regression itself, so it exits 2 rather than 1: a gate
   whose owner stopped enforcing cannot report on anything else honestly.

2. **Nothing else derives a role roster.** No other function or property
   RETURNS a collection built from agent roles: a set / list / generator
   comprehension whose element is ``<x>.role`` (bare or through ``str(...)``),
   however it is wrapped in ``tuple`` / ``sorted`` / ``set`` / ``frozenset`` /
   ``list``. That is exactly the shape that drifted: the recursion-depth
   sweep's ``SweepRoster.roles`` carried its own copy of the comprehension
   over builders AND reviewers, and it is what fed the sweep's own planner,
   so the rule was enforced in the product and bypassed in the harness
   measuring it.

The rule is about DERIVING a roster, not about passing one on. Nine of the
eleven ``available_roles=`` sites in the tree are pass-throughs of a roster
built upstream (a parameter, a context field, a local), so a rule written
against the keyword would be noise, and a value that reached one of them
without passing through the owner had to be derived somewhere this catches.

Allowlist / opt-out
-------------------
Per-line opt-out: append ``# lint-allow: gate-role-assignable -- <reason>`` to
the ``return`` line. The justification after ``--`` is required, because every
legitimate case is the claim that this particular collection of roles never
reaches a planner, and the marker is the only place that claim is written down.

There is deliberately no baseline. A roster offering a judge as an executor is
never something to preserve.

Usage::

    uv run python scripts/check_gate_roles_not_assignable.py
    uv run python scripts/check_gate_roles_not_assignable.py --files a.py b.py

Exit codes:
    0 -- one owner, and it excludes gate roles.
    1 -- a second roster derivation.
    2 -- configuration error: a bad ``--repo-root``, an owner that no longer
         exists or no longer calls ``role_is_gate_role``, or a source file that
         could not be read, parsed or tokenised (fail-closed).
"""

import argparse
import ast
import io
import subprocess
import sys
import tokenize
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
_SCAN_ROOTS_REL: Final[tuple[str, ...]] = ("src/synthorg", "evals")
_SUPPRESSION_MARKER: Final[str] = "lint-allow: gate-role-assignable"

#: Where the single owner lives, and what it must still call. Both halves are
#: checked: an owner that stopped excluding gate roles reads exactly like an
#: owner that never did.
_OWNER_REL: Final[str] = "src/synthorg/engine/decomposition/context.py"
_OWNER_FUNC: Final[str] = "roster_from_agents"
_OWNER_GUARD: Final[str] = "role_is_gate_role"

#: The attribute that names an agent's role. A collection of these IS a roster.
_ROLE_ATTR: Final[str] = "role"

#: Calls that pass a collection through unchanged, so a roster wrapped in one
#: is still a roster. ``str`` is here for the element position, where
#: ``str(agent.role)`` is the same read spelled defensively.
_TRANSPARENT_CALLS: Final[frozenset[str]] = frozenset(
    {"tuple", "sorted", "set", "frozenset", "list", "str"}
)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


class OwnerError(Exception):
    """Raised when the declared owner is missing or no longer enforcing."""


@dataclass(frozen=True)
class _Hit:
    """One function returning a roster the owner did not derive."""

    rel: str
    lineno: int
    col: int
    qualname: str

    def message(self) -> str:
        """Return the human-facing violation message.

        Returns:
            The message naming the file, the line and the fix.
        """
        return (
            f"{self.rel}:{self.lineno}:{self.col}: {self.qualname}() returns a "
            f"roster it derives from agent roles itself. A gate role is "
            f"staffed, so a roster built here offers a judge as an executor. "
            f"Call {_OWNER_FUNC}(), which is the one owner of which roles may "
            f"own a plan item."
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
    if not abs_root.is_dir():
        return []
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
            f"check_gate_roles_not_assignable: git ls-files failed in "
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


def _is_valid_marker(comment_token: str) -> bool:
    """Return True iff *comment_token* is a justified suppression marker.

    Returns:
        ``True`` for ``# lint-allow: gate-role-assignable -- <reason>``.
    """
    comment = comment_token.lstrip("#").strip()
    if not comment.startswith(_SUPPRESSION_MARKER):
        return False
    suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
    return suffix.startswith("--") and bool(suffix[2:].strip())


def _marker_lines(text: str, rel: str) -> set[int]:
    """Return the 1-indexed line numbers carrying a valid suppression marker.

    Returns:
        The set of line numbers whose comment is a justified marker.

    Raises:
        GateSourceError: If the source fails to tokenise, so a dropped marker
            fails the gate loud rather than silently.
    """
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and _is_valid_marker(tok.string):
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        msg = f"{rel}: could not tokenise source: {exc}"
        raise GateSourceError(msg) from exc
    return lines


def _called_name(node: ast.Call) -> str | None:
    """Return the simple name a call targets, or None when it is not simple.

    Returns:
        The function name for ``f(...)`` or the attribute for ``a.b(...)``.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _unwrap(value: ast.expr) -> ast.expr:
    """Strip the wrappers that leave a collection's contents unchanged.

    Returns:
        The innermost expression a transparent wrapper chain encloses.
    """
    current = value
    while (
        isinstance(current, ast.Call)
        and _called_name(current) in _TRANSPARENT_CALLS
        and current.args
    ):
        current = current.args[0]
    return current


def _reads_role(value: ast.expr) -> bool:
    """Return True iff *value* reads an object's ``role`` attribute.

    Returns:
        ``True`` for ``x.role`` and for ``str(x.role)``.
    """
    inner = _unwrap(value)
    return isinstance(inner, ast.Attribute) and inner.attr == _ROLE_ATTR


def _derives_roster(value: ast.expr) -> bool:
    """Return True iff *value* builds a collection out of agent roles.

    A dict comprehension is deliberately not one: it maps something ONTO a
    role rather than producing the roles themselves, and reading it as a
    roster would flag an id-to-role index that no planner ever sees.

    Returns:
        ``True`` when the expression is a role comprehension, however wrapped.
    """
    inner = _unwrap(value)
    if isinstance(inner, ast.SetComp | ast.ListComp | ast.GeneratorExp):
        return _reads_role(inner.elt)
    return False


def _returned_values(scope: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Return]:
    """Return every ``return`` belonging to *scope*'s own body.

    Nested functions own their returns, so the walk stops at one: a helper
    defined inside a function is a separate scope and is visited in its own
    right.

    Returns:
        The return statements *scope* itself executes.
    """
    found: list[ast.Return] = []
    stack: list[ast.AST] = list(scope.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.ClassDef):
            continue
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _functions(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return every function in *tree* with its dotted qualname.

    Descends through EVERY node rather than through class and function bodies
    alone: a ``def`` inside an ``if``, a ``try`` or a ``with`` is a function
    like any other, and stopping at those three statement types would leave a
    whole shape of definition unscanned by a gate that carries no baseline and
    no opt-out, whose only claim is that it sees everything. Only a scope
    boundary extends the qualname prefix, so a function defined under a
    conditional keeps the qualname of the scope it actually belongs to.

    Returns:
        ``(qualname, node)`` pairs, classes included, nested defs included.
    """
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append((f"{prefix}{child.name}", child))
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _check_owner(project_root: Path) -> None:
    """Verify the declared owner exists and still excludes gate roles.

    Raises:
        OwnerError: The owner module, the function, or its call to
            ``role_is_gate_role`` is gone.
        GateSourceError: The owner module cannot be read or parsed.
    """
    path = project_root / _OWNER_REL
    if not path.is_file():
        msg = f"declared owner {_OWNER_REL} does not exist"
        raise OwnerError(msg)
    _, tree = read_and_parse(path)
    owners = [node for name, node in _functions(tree) if name == _OWNER_FUNC]
    if not owners:
        msg = f"{_OWNER_REL} no longer defines {_OWNER_FUNC}()"
        raise OwnerError(msg)
    guarded = any(
        isinstance(node, ast.Call) and _called_name(node) == _OWNER_GUARD
        for owner in owners
        for node in ast.walk(owner)
    )
    if not guarded:
        msg = (
            f"{_OWNER_REL}::{_OWNER_FUNC}() no longer calls {_OWNER_GUARD}(), so "
            f"every roster it answers with offers a judge as an executor"
        )
        raise OwnerError(msg)


def _scan_file(path: Path, rel: str) -> list[_Hit]:
    """Return every second roster derivation in one file.

    Returns:
        A list of :class:`_Hit`, one per offending ``return``.

    Raises:
        GateSourceError: If the file cannot be read, parsed or tokenised.
    """
    text, tree = read_and_parse(path)
    marked = _marker_lines(text, rel)
    hits: list[_Hit] = []
    for qualname, func in _functions(tree):
        if rel == _OWNER_REL and qualname == _OWNER_FUNC:
            continue
        for stmt in _returned_values(func):
            if stmt.value is None or not _derives_roster(stmt.value):
                continue
            # Matched anywhere in the statement's line span: a trailing comment
            # on a wrapped return sits on its LAST line, not its first.
            span = range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1)
            if any(line in marked for line in span):
                continue
            hits.append(
                _Hit(
                    rel=rel,
                    lineno=stmt.lineno,
                    col=stmt.col_offset,
                    qualname=qualname,
                )
            )
    return hits


def _scan_all(project_root: Path, files: list[Path] | None) -> list[_Hit]:
    """Scan the declared roots, or just *files* when given.

    Returns:
        A list of :class:`_Hit`.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    hits: list[_Hit] = []
    if files is not None:
        for path in files:
            resolved = path.resolve()
            try:
                rel = resolved.relative_to(project_root).as_posix()
            except ValueError:
                continue
            if resolved.suffix == ".py" and resolved.is_file():
                hits.extend(_scan_file(resolved, rel))
        return hits
    for root_rel in _SCAN_ROOTS_REL:
        abs_root = project_root / root_rel
        for path, rel in _git_tracked_python_files(abs_root, project_root):
            hits.extend(_scan_file(path, rel))
    return hits


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
    parser.add_argument(
        "--files",
        nargs="*",
        type=Path,
        default=None,
        help="Scan only these files (the owner check still runs).",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        _check_owner(project_root)
        hits = _scan_all(project_root, args.files)
    except OwnerError as exc:
        print(f"check_gate_roles_not_assignable: {exc}", file=sys.stderr)
        return 2
    except GateSourceError as exc:
        print(f"check_gate_roles_not_assignable: {exc}", file=sys.stderr)
        return 2

    if not hits:
        return 0
    hits.sort(key=lambda h: (h.rel, h.lineno, h.col))
    for hit in hits:
        print(hit.message())
    print(
        f"\n{len(hits)} roster derivation(s) outside {_OWNER_FUNC}(). Call it "
        "instead, or add '# lint-allow: gate-role-assignable -- <reason>' on "
        "the return line when the roles never reach a planner.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
