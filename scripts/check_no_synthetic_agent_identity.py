#!/usr/bin/env python3
"""Pre-push / CI gate: an ``AgentIdentity`` is a roster member, not a prop.

Authority in this product comes from a role a real agent holds. An identity
constructed in place holds a role nobody granted: it is registered nowhere,
staffed by nobody, on no project team, and absent from ``GET /agents/active``.

The consequence is not cosmetic. Work judged by such a thing is judged by
something that is not a peer: nobody can be given the role, no operator can see
who holds it, and the verdicts cannot be compared per agent or per model the
way every other agent's work can. This gate is what keeps the construct out,
because it is a single call that reads perfectly reasonable in isolation.

Detection
---------
AST-walk every tracked ``*.py`` under ``src/synthorg/`` and flag any identity
construction outside the declared roster-construction paths. Those three paths
are the only places an identity legitimately comes into being, and each has its
reason written down beside it below.

A construction is calling the class, under whatever local name it was imported
as, or calling one of Pydantic's class-level constructors on it
(``model_validate``, ``model_validate_json``, ``model_construct``). Matching one
spelling would be a gate you get past by renaming an import, and with no
baseline the detection is the whole guarantee.

The gate also fails when a declared path stops constructing one: a declaration
that has outlived its site is an exemption nobody is using, and the next
construction added to that module would inherit it silently.

What it does NOT do
-------------------
It says nothing about what the identity is used for, which no AST can decide.
The complement is the roster itself: an identity that is registered is visible,
staffable and comparable, and one that is not is invisible by construction.

An instance-level ``model_copy`` is deliberately not a construction. It derives
from an identity that already exists, so whatever it produces started life on
the roster, and narrowing a selected agent for one dispatch is exactly how a
gate is meant to work.

Allowlist / opt-out
-------------------
Per-line opt-out: append ``# lint-allow: synthetic-agent-identity -- <reason>``
to the construction line. The justification after ``--`` is required.

There is deliberately no baseline file. The tree holds exactly the declared
sites, and a baseline would only be a place for the next one to hide.

Usage::

    uv run python scripts/check_no_synthetic_agent_identity.py

Exit codes:
    0 -- every construction sits in a declared roster path.
    1 -- an identity is constructed outside the roster.
    2 -- configuration error (bad ``--repo-root``, a declared path that no
         longer constructs one, or a source file that could not be read,
         parsed, or tokenised -- fail-closed).
"""

import argparse
import ast
import io
import subprocess
import sys
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
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
_SUPPRESSION_MARKER: Final[str] = "lint-allow: synthetic-agent-identity"
_TARGET_CLASS: Final[str] = "AgentIdentity"
#: The module that defines the class. Tracked because reaching the class
#: through its module (``agent.AgentIdentity(...)``) mints exactly the same
#: identity as importing the name, so a gate blind to it is a gate you get
#: past by changing an import line.
_TARGET_MODULE: Final[str] = "synthorg.core.agent"

#: Pydantic's class-level constructors. Each takes untyped input and hands
#: back a whole identity, so each is the same mint as calling the class;
#: ``model_construct`` is the sharpest of them, skipping validation outright.
_CLASS_CONSTRUCTORS: Final[frozenset[str]] = frozenset(
    {"model_validate", "model_validate_json", "model_construct"}
)

# The three modules that turn something into a roster member, each with the
# reason it is allowed to. Written here rather than as bare paths because the
# reason is the whole content of the exemption.
_ROSTER_CONSTRUCTION_PATHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "src/synthorg/api/bootstrap.py": (
            "turns persisted AgentConfig rows into the roster at boot"
        ),
        "src/synthorg/hr/hiring_candidates.py": (
            "turns an approved hire into a roster member"
        ),
        "src/synthorg/meta/chief_of_staff/console_identity.py": (
            "the operator's own cockpit, which is not an org member: it "
            "neither performs nor judges org work, it configures the control "
            "plane, and it is governed per action by the SecOps gate rather "
            "than by roster membership"
        ),
    }
)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One ``AgentIdentity`` constructed outside the roster."""

    rel: str
    lineno: int
    col: int

    def message(self) -> str:
        """Return the human-facing violation message.

        Returns:
            The formatted violation line.
        """
        return (
            f"{self.rel}:{self.lineno}:{self.col}: constructs an "
            f"{_TARGET_CLASS} outside the roster. An identity nobody "
            f"registered is invisible in the roster, cannot be given its role "
            f"by an operator, and its work cannot be compared with any other "
            f"agent's. Select a roster agent instead."
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
            f"check_no_synthetic_agent_identity: git ls-files failed in "
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
        ``True`` for ``# lint-allow: synthetic-agent-identity -- <reason>``.
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


def _dotted(node: ast.expr) -> str | None:
    """Render a pure name/attribute chain as its dotted source spelling.

    Returns:
        ``"agent.AgentIdentity"`` for that expression, ``None`` for anything
        rooted in a call, subscript or literal, where no static spelling
        exists to compare against.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _record_alias(
    node: ast.Assign | ast.AnnAssign, names: set[str], factories: set[str]
) -> None:
    """Track a name bound to the class, or to one of its constructors.

    The second half is what closes the hole the first alone leaves: binding
    ``AgentIdentity.model_construct`` to a name hands the caller a callable
    that mints an identity with the class spelled nowhere near it. A value
    rooted in a call or a subscript has no static spelling to carry forward,
    so it is ignored rather than guessed at.

    An annotation is not a different binding, only a different node type, so
    ``factory: object = ...`` is read exactly as ``factory = ...``; a bare
    ``factory: object`` binds nothing and carries nothing forward.

    Args:
        node: The assignment to inspect.
        names: Class spellings, extended in place.
        factories: Constructor aliases, extended in place.
    """
    value = None if node.value is None else _dotted(node.value)
    if value is None:
        return
    base, _, attr = value.rpartition(".")
    if value in names:
        bucket = names
    elif value in factories or (attr in _CLASS_CONSTRUCTORS and base in names):
        bucket = factories
    else:
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    bucket.update(t.id for t in targets if isinstance(t, ast.Name))


def _local_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return every local spelling that reaches the target class.

    A gate that matched one spelling would be a gate you get past by
    renaming the import, and with no baseline the detection IS the whole
    guarantee. Covers ``from ... import AgentIdentity as X``, a module-level
    ``X = AgentIdentity``, and every way of reaching the class through its
    module: ``import synthorg.core.agent [as a]`` and
    ``from synthorg.core import agent [as a]``, each yielding the dotted
    spelling the call site actually writes.

    Args:
        tree: The parsed module.

    Returns:
        ``(names, factories)``: the dotted spellings that refer to the class,
        and the names bound to one of its class-level constructors, whose
        call mints an identity just as directly.
    """
    names = {_TARGET_CLASS}
    factories: set[str] = set()
    package, _, leaf = _TARGET_MODULE.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name != _TARGET_MODULE:
                    continue
                # Without ``as``, the binding is the root package but the
                # written spelling is the full dotted path, which is what a
                # call site is compared against.
                names.add(f"{alias.asname or alias.name}.{_TARGET_CLASS}")
        elif isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname
                for alias in node.names
                if alias.name == _TARGET_CLASS and alias.asname
            )
            if node.module == package:
                names.update(
                    f"{alias.asname or alias.name}.{_TARGET_CLASS}"
                    for alias in node.names
                    if alias.name == leaf
                )
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            _record_alias(node, names, factories)
    return names, factories


def _is_construction(node: ast.Call, names: set[str], factories: set[str]) -> bool:
    """Return whether *node* mints an identity out of nothing.

    Three shapes count: calling the class, calling one of Pydantic's
    class-level constructors on it, and calling a name one of those
    constructors was bound to. ``model_construct`` matters most of the three,
    because it skips validation entirely. Each is matched on the dotted
    spelling, so reaching the class through its module counts exactly as
    reaching it through an imported name.

    An instance-level ``model_copy`` is deliberately NOT a construction: it
    derives from an identity that already exists, so whatever it produces
    started life on the roster, and narrowing a selected agent for a
    dispatch is exactly how a gate is supposed to work.

    Args:
        node: The call to classify.
        names: Local spellings that refer to the class.
        factories: Local names bound to one of its constructors.

    Returns:
        ``True`` when the call mints an identity.
    """
    func = node.func
    dotted = _dotted(func)
    if dotted in names or dotted in factories:
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _CLASS_CONSTRUCTORS
        and _dotted(func.value) in names
    )


def _construction_lines(tree: ast.Module) -> list[tuple[int, int]]:
    """Return the ``(line, column)`` of every identity construction.

    Returns:
        One entry per construction, in walk order.
    """
    names, factories = _local_names(tree)
    return [
        (node.lineno, node.col_offset)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_construction(node, names, factories)
    ]


def _scan_file(path: Path, rel: str) -> tuple[list[_Hit], int]:
    """Return the violations in one file and how many constructions it holds.

    Returns:
        ``(hits, construction_count)``. A declared roster path yields no hits,
        but its count still matters: it is what proves the declaration is
        still load-bearing.

    Raises:
        GateSourceError: If the file cannot be read or parsed (fail-closed).
    """
    text, tree = read_and_parse(path)
    sites = _construction_lines(tree)
    if not sites:
        return [], 0
    if rel in _ROSTER_CONSTRUCTION_PATHS:
        return [], len(sites)
    marked = _marker_lines(text, rel)
    hits = [
        _Hit(rel=rel, lineno=lineno, col=col)
        for lineno, col in sites
        if lineno not in marked
    ]
    return hits, len(sites)


def _scan_all(project_root: Path) -> tuple[list[_Hit], set[str]]:
    """Scan ``src/synthorg`` for constructions outside the roster.

    Returns:
        ``(hits, declared_paths_that_still_construct)``.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    hits: list[_Hit] = []
    live_declared: set[str] = set()
    for path, rel in _git_tracked_python_files(abs_root, project_root):
        file_hits, count = _scan_file(path, rel)
        hits.extend(file_hits)
        if count and rel in _ROSTER_CONSTRUCTION_PATHS:
            live_declared.add(rel)
    return hits, live_declared


def _report_stale_declarations(live_declared: set[str]) -> int:
    """Print every declared path that no longer constructs an identity.

    Returns:
        The number of stale declarations.
    """
    stale = sorted(set(_ROSTER_CONSTRUCTION_PATHS) - live_declared)
    for rel in stale:
        print(
            f"{rel}: declared as a roster-construction path but constructs no "
            f"{_TARGET_CLASS}. Remove the declaration, or point it at the "
            f"module that took the construction over: an unused exemption is "
            f"one the next construction inherits silently.",
            file=sys.stderr,
        )
    return len(stale)


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
        hits, live_declared = _scan_all(project_root)
    except GateSourceError as exc:
        print(f"check_no_synthetic_agent_identity: {exc}", file=sys.stderr)
        return 2

    if _report_stale_declarations(live_declared):
        return 2

    if not hits:
        return 0
    hits.sort(key=lambda h: (h.rel, h.lineno, h.col))
    for hit in hits:
        print(hit.message())
    print(
        f"\n{len(hits)} {_TARGET_CLASS} construction(s) outside the roster. "
        "Select a registered agent holding the role instead, or add "
        "'# lint-allow: synthetic-agent-identity -- <reason>' on the "
        "construction line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
