#!/usr/bin/env python3
"""Pre-push / CI error-code-uniqueness gate.

Enforces the rule: each ``ErrorCode`` value maps to exactly one
``DomainError`` subclass, so a client branching on ``error_code`` can
discriminate a single condition. Two structurally-unrelated classes
declaring the same ``error_code`` is a contract bug.

Two exemptions keep the rule honest:

1. **Inheritance alias.** A subclass that redeclares an ancestor's code
   is fine -- it is the same condition narrowed (e.g.
   ``ResourceNotFoundError(NotFoundError)`` both
   ``RESOURCE_NOT_FOUND``). A group sharing a code is legal when one
   declarer is an ancestor of every other declarer.

2. **Shareable generic codes.** The per-category fallback codes
   (``INTERNAL_ERROR``, ``VALIDATION_ERROR``, ``RESOURCE_NOT_FOUND``,
   ...) are deliberately reused by many distinct base classes. They are
   listed in :data:`SHAREABLE_CODES`; a code there may be carried by any
   number of unrelated classes.

Sanctioned one-off exceptions opt out via a per-line trailing comment on
the class header::

    class Foo(DomainError):  # lint-allow: error-code-uniqueness -- <reason>

The justification after ``--`` is required and must be non-empty. There
is no baseline file: the rule ships with zero offenders.

Usage::

    python scripts/check_error_code_uniqueness.py
    python scripts/check_error_code_uniqueness.py --repo-root PATH
"""

import argparse
import ast
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

# ── Constants ────────────────────────────────────────────────────

_ERROR_CODE_ENUM: Final[str] = "ErrorCode"

SUPPRESSION_MARKER: Final[str] = "lint-allow: error-code-uniqueness"

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*error-code-uniqueness\s*--\s*\S",
)

# Generic per-category fallback codes that many unrelated classes share
# on purpose. Each names a category default, not a specific resource or
# condition, so reuse does not break client ``error_code`` branching.
# Adding a SPECIFIC code here (one that names a single resource /
# condition) defeats the gate -- keep this list to category fallbacks.
SHAREABLE_CODES: Final[frozenset[str]] = frozenset(
    {
        # Per-category generic fallbacks.
        "INTERNAL_ERROR",
        "VALIDATION_ERROR",
        "REQUEST_VALIDATION_ERROR",
        "RESOURCE_NOT_FOUND",
        "RESOURCE_CONFLICT",
        "SERVICE_UNAVAILABLE",
        "PERSISTENCE_ERROR",
        "RECORD_NOT_FOUND",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "RATE_LIMITED",
        "PROVIDER_ERROR",
        # Tool-subsystem fallbacks shared by every tool domain (browser /
        # desktop / external-api) for the same client-facing condition.
        "TOOL_EXECUTION_ERROR",
        "TOOL_PARAMETER_ERROR",
    }
)

_SCAN_REL_DEFAULT: Final[str] = "src/synthorg"

# A code carried by fewer than two declarers cannot be a duplicate.
_MIN_DUPLICATE_GROUP: Final[int] = 2

_GIT_INHERITED_ENV_VARS: Final[tuple[str, ...]] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
)


# ── Suppression marker ───────────────────────────────────────────


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker with a non-empty reason."""
    return bool(_SUPPRESSION_RE.search(line))


# ── Module path resolver ─────────────────────────────────────────


def _module_dotted_for_rel(rel: str) -> str:
    """Return the dotted module path for a repo-relative POSIX path."""
    posix = rel.replace("\\", "/")
    posix = posix.removeprefix("src/")
    posix = posix.removesuffix(".py")
    parts = posix.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ── AST resolution (mirrors check_domain_error_hierarchy) ─────────


def _build_alias_map(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    """Return ``{local_name: (module_dotted, original_name_or_None)}``."""
    aliases: dict[str, tuple[str, str | None]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = (alias.name, None)
                else:
                    head = alias.name.split(".")[0]
                    aliases.setdefault(head, (head, None))
    return aliases


def _resolve_base(
    node: ast.expr,
    alias_map: dict[str, tuple[str, str | None]],
    current_module: str,
) -> tuple[str, str] | None:
    """Resolve a base-class AST node to ``(module, name)`` or ``None``."""
    if isinstance(node, ast.Name):
        if node.id in alias_map:
            module, original = alias_map[node.id]
            if original is None:
                return None
            return (module, original)
        return (current_module, node.id)
    if isinstance(node, ast.Attribute):
        attrs: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        head = cur.id
        attrs.reverse()
        if head not in alias_map:
            return None
        module, original = alias_map[head]
        if original is not None:
            module = f"{module}.{original}"
        if len(attrs) == 1:
            return (module, attrs[0])
        return (module + "." + ".".join(attrs[:-1]), attrs[-1])
    return None


def _declared_error_code(node: ast.ClassDef) -> str | None:
    """Return the ``ErrorCode`` member name the class body declares, if any.

    Matches ``error_code = ErrorCode.NAME`` and
    ``error_code: ClassVar[ErrorCode] = ErrorCode.NAME`` (or any
    annotation). Returns the member name (e.g. ``"PROJECT_NOT_FOUND"``)
    or ``None`` when the class does not statically assign a literal
    ``ErrorCode`` member.
    """
    for stmt in node.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target = stmt.target
            value = stmt.value
        elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            first = stmt.targets[0]
            if isinstance(first, ast.Name):
                target = first
                value = stmt.value
        if target is None or not isinstance(target, ast.Name):
            continue
        if target.id != "error_code" or value is None:
            continue
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == _ERROR_CODE_ENUM
        ):
            return value.attr
    return None


# ── Scanning ─────────────────────────────────────────────────────


class _ClassEntry:
    """Indexed metadata for one ``class`` definition."""

    __slots__ = ("bases", "code", "key", "lineno", "name", "rel", "suppressed")

    def __init__(  # noqa: PLR0913 -- frozen dataclass-style init; keyword-only
        self,
        *,
        rel: str,
        lineno: int,
        key: tuple[str, str],
        name: str,
        bases: list[tuple[str, str]],
        code: str | None,
        suppressed: bool,
    ) -> None:
        self.rel = rel
        self.lineno = lineno
        self.key = key
        self.name = name
        self.bases = bases
        self.code = code
        self.suppressed = suppressed


def _iter_class_defs(tree: ast.Module) -> Iterator[ast.ClassDef]:
    """Yield every ``ClassDef`` except those nested inside a function body.

    ``ast.walk`` dominates the gate's runtime on the full tree because it
    visits every node. Two prunes cut the bulk without missing an error
    class:

    * ``ast.expr`` subtrees -- a ``ClassDef`` is a statement, and a statement
      can never appear inside an expression, so module-level literals (the
      large dict / list / call values common in this codebase), decorators
      and default-arg values hold no class to find.
    * ``FunctionDef`` / ``AsyncFunctionDef`` bodies -- ``DomainError``
      subclasses are only ever module-level, class-nested, or inside a
      module-level conditional (``if`` / ``try``); never function-local.
    """
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield child
                stack.append(child)
            elif not isinstance(
                child, ast.expr | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                stack.append(child)


def _index_file(path: Path, rel: str) -> tuple[list[_ClassEntry], str | None]:
    """Return every class-definition entry in *path* (and parse error, if any)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"{rel}:0: unable to read file: {exc}"
    # A file with no ``class`` keyword holds no ``ClassDef``, so it
    # contributes neither an error-code declarer nor an ancestry node.
    # Skipping the parse for those (the majority of utility / constant
    # modules) avoids the gate's now-dominant cost. The substring can only
    # over-include (a ``class`` in a comment / string -> a harmless parse),
    # never under-include: every real ``ClassDef`` carries the keyword.
    if "class " not in text:
        return [], None
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [], f"{rel}:{exc.lineno or 0}: unable to parse file: {exc.msg}"
    lines = text.splitlines()
    alias_map = _build_alias_map(tree)
    module = _module_dotted_for_rel(rel)
    entries: list[_ClassEntry] = []
    for node in _iter_class_defs(tree):
        bases = [
            resolved
            for b in node.bases
            if (resolved := _resolve_base(b, alias_map, module)) is not None
        ]
        entries.append(
            _ClassEntry(
                rel=rel,
                lineno=node.lineno,
                key=(module, node.name),
                name=node.name,
                bases=bases,
                code=_declared_error_code(node),
                suppressed=_class_def_suppressed(node, lines),
            )
        )
    return entries, None


def _class_def_suppressed(node: ast.ClassDef, lines: list[str]) -> bool:
    """Return True iff any line of the class header carries the marker."""
    start = node.lineno
    end = max(start, node.body[0].lineno - 1) if node.body else start
    for lineno in range(start, end + 1):
        if 1 <= lineno <= len(lines) and _line_has_trailing_marker(lines[lineno - 1]):
            return True
    return False


def _compute_ancestors(
    entries: list[_ClassEntry],
) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """Return ``{class_key: {ancestor_keys}}`` over the indexed tree.

    Only intra-tree bases resolve; external bases (``Exception``,
    third-party) are absent from the index and contribute no ancestry.
    """
    by_key: dict[tuple[str, str], _ClassEntry] = {e.key: e for e in entries}
    ancestors: dict[tuple[str, str], set[tuple[str, str]]] = {}

    def resolve(
        key: tuple[str, str], seen: frozenset[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        if key in ancestors:
            return ancestors[key]
        if key in seen or key not in by_key:
            return set()
        acc: set[tuple[str, str]] = set()
        for base in by_key[key].bases:
            acc.add(base)
            acc |= resolve(base, seen | {key})
        ancestors[key] = acc
        return acc

    for entry in entries:
        resolve(entry.key, frozenset())
    return ancestors


def _group_is_aliased(
    members: list[_ClassEntry],
    ancestors: dict[tuple[str, str], set[tuple[str, str]]],
) -> bool:
    """Return True iff one member is an ancestor of every other member."""
    for candidate in members:
        if all(
            other.key == candidate.key
            or candidate.key in ancestors.get(other.key, set())
            for other in members
        ):
            return True
    return False


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` file under *abs_root* as ``(abs, rel)``."""
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    env = os.environ.copy()
    for name in _GIT_INHERITED_ENV_VARS:
        env.pop(name, None)
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{rel_root}/*.py"],
            check=True,
            capture_output=True,
            cwd=project_root,
            env=env,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        # git unavailable / not a repo: scan every .py on disk instead of
        # the tracked set. Announce it so a wider scan (untracked / generated
        # files) is not mistaken for the normal tracked-only run.
        print(
            f"check_error_code_uniqueness: git ls-files unavailable for "
            f"{rel_root!r}; falling back to rglob (scans untracked files too).",
            file=sys.stderr,
        )
        return [
            (p, p.relative_to(project_root).as_posix())
            for p in sorted(abs_root.rglob("*.py"))
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _scan_tree(project_root: Path, scan_root: Path) -> list[str]:
    """Run the full scan; return a sorted list of violation messages."""
    files = _git_tracked_python_files(scan_root, project_root)
    all_entries: list[_ClassEntry] = []
    parse_errors: list[str] = []
    for path, rel in files:
        entries, error = _index_file(path, rel)
        if error is not None:
            parse_errors.append(error)
            continue
        all_entries.extend(entries)

    ancestors = _compute_ancestors(all_entries)
    # Group ALL code-bearing classes (including suppressed ones) so a
    # suppressed parent still counts as an ancestor candidate for the
    # alias check below; only non-suppressed members are ever reported.
    by_code: dict[str, list[_ClassEntry]] = {}
    for entry in all_entries:
        if entry.code is None:
            continue
        by_code.setdefault(entry.code, []).append(entry)

    messages = sorted(parse_errors)
    for code, group in sorted(by_code.items()):
        reportable = [m for m in group if not m.suppressed]
        if len(reportable) < _MIN_DUPLICATE_GROUP or code in SHAREABLE_CODES:
            continue
        if _group_is_aliased(group, ancestors):
            continue
        locations = ", ".join(
            f"{m.rel}:{m.lineno} ({m.name})"
            for m in sorted(reportable, key=lambda m: m.key)
        )
        messages.append(
            f"ErrorCode.{code} is declared by {len(reportable)} unrelated classes: "
            f"{locations}. Each distinct condition needs its own ErrorCode, or "
            "merge the classes (inheritance alias). If this code is a generic "
            "category fallback, add it to SHAREABLE_CODES; otherwise add "
            "'# lint-allow: error-code-uniqueness -- <reason>' on the class header. "
            "See docs/reference/errors.md."
        )
    return messages


# ── CLI ─────────────────────────────────────────────────────────


class ProjectRootError(
    Exception
):  # lint-allow: domain-error-hierarchy -- gate-internal CLI error; never leaves this script
    """Raised when ``--repo-root`` cannot be resolved."""


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments."""
    default_root = Path(__file__).resolve().parent.parent
    if repo_root is None:
        return default_root
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def _resolve_scan_root(project_root: Path, scan_path: str) -> Path | None:
    """Resolve *scan_path* to an absolute path strictly under *project_root*."""
    candidate = Path(scan_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    project_root_str = os.fspath(project_root.resolve(strict=False))
    resolved_str = os.fspath(resolved)
    try:
        common = os.path.commonpath([project_root_str, resolved_str])
    except ValueError:
        return None
    if common != project_root_str:
        return None
    return resolved


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        default=_SCAN_REL_DEFAULT,
        help="Root to scan (relative to repo root); cross-module resolution walks it whole.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root to anchor path resolution against.",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    scan_root = _resolve_scan_root(project_root, args.paths)
    if scan_root is None or not scan_root.exists():
        print(
            f"refusing to scan path outside project root or missing: {args.paths}",
            file=sys.stderr,
        )
        return 2

    messages = _scan_tree(project_root, scan_root)
    if messages:
        for msg in messages:
            print(msg)
        print(
            f"\n{len(messages)} error-code-uniqueness violation(s) found. "
            "See docs/reference/errors.md for the ErrorCode contract.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
