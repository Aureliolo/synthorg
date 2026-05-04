#!/usr/bin/env python3
"""Pre-push / CI domain-error-hierarchy gate.

Enforces the rule: every exception class defined in ``src/synthorg/``
inherits from :class:`synthorg.core.domain_errors.DomainError` (directly
or via an intermediate that itself reaches ``DomainError``).

Plain stdlib bases (``Exception`` / ``RuntimeError`` / ``LookupError`` /
``PermissionError`` / ``ValueError`` / ``TypeError`` / ``KeyError`` /
``IndexError`` / ``AttributeError`` / ``OSError`` / ``IOError``) are
forbidden as a *direct* base for any class in ``src/synthorg/``. Only
the root of a bad inheritance chain is flagged; migrating the root
automatically fixes every transitive descendant.

Sanctioned exceptions (e.g. the ``DomainError`` root itself, RFC 3161
internals) opt out via a per-line trailing comment::

    class Foo(Exception):  # lint-allow: domain-error-hierarchy -- <reason>

The justification after ``--`` is required and must be non-empty.

The script also accepts a frozen baseline file
(``scripts/domain_error_hierarchy_baseline.txt``) listing pre-existing
violations the gate should ignore. Entries are formatted
``<posix_path>:<lineno>:<class_name>``. A baseline entry that no longer
maps to a real violation is reported as drift -- the baseline must
shrink monotonically.

Usage::

    python scripts/check_domain_error_hierarchy.py
    python scripts/check_domain_error_hierarchy.py --no-baseline
    python scripts/check_domain_error_hierarchy.py --update-baseline
"""

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

# ── Constants ────────────────────────────────────────────────────

FORBIDDEN_BASES: Final[frozenset[str]] = frozenset(
    {
        "Exception",
        "RuntimeError",
        "LookupError",
        "PermissionError",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "OSError",
        "IOError",
    }
)

SUPPRESSION_MARKER: Final[str] = "lint-allow: domain-error-hierarchy"

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*domain-error-hierarchy\s*--\s*\S",
)

_DOMAIN_ERROR_MODULE: Final[str] = "synthorg.core.domain_errors"
_DOMAIN_ERROR_NAME: Final[str] = "DomainError"
_FORBIDDEN_PSEUDO_MODULE: Final[str] = "__forbidden__"

_BASELINE_REL_PATH: Final[str] = "scripts/domain_error_hierarchy_baseline.txt"

_BASELINE_HEADER = """\
# Frozen baseline of pre-existing plain-Exception class definitions in
# src/synthorg/. Each line is `path:lineno:class_name` (POSIX path,
# 1-indexed line) sorted in deterministic order.
#
# scripts/check_domain_error_hierarchy.py reads this file to suppress
# violations at these exact entries. New violations NOT in this list
# will fail the pre-push hook.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_domain_error_hierarchy.py --update-baseline
"""

_BASELINE_ENTRY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^.+:\d+:\w+$")

_SCAN_REL_DEFAULT: Final[str] = "src/synthorg"


# ── Suppression marker ───────────────────────────────────────────


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing comment.

    The marker must be followed by ``--`` and non-empty justification
    text -- ``# lint-allow: domain-error-hierarchy -- TSA RFC 3161 client``.

    Regex-based on purpose: ruff-format may split a long class header
    across multiple physical lines, leaving the marker on a continuation
    line that ``tokenize.generate_tokens`` can't parse in isolation
    (``):  # marker`` is not a valid Python statement). The full
    comment-shape regex below works on any line fragment.
    """
    return bool(_SUPPRESSION_RE.search(line))


# ── Module path resolver ─────────────────────────────────────────


def _module_dotted_for_rel(rel: str) -> str:
    """Return the dotted module path for a repo-relative POSIX path.

    ``src/synthorg/foo/bar.py``        -> ``synthorg.foo.bar``
    ``src/synthorg/foo/__init__.py``   -> ``synthorg.foo``
    ``src/synthorg/__init__.py``       -> ``synthorg``
    """
    posix = rel.replace("\\", "/")
    posix = posix.removeprefix("src/")
    posix = posix.removesuffix(".py")
    parts = posix.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ── AST resolution ───────────────────────────────────────────────


def _build_alias_map(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    """Return ``{local_name: (module_dotted, original_name_or_None)}``.

    ``original_name`` is ``None`` when the local name aliases a whole
    module (``import X as Y`` or ``import X``); otherwise it is the
    original symbol name imported from the module.

    Module-level imports only -- function-scoped imports are ignored.
    """
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


def _resolve_base(  # noqa: C901, PLR0911 -- AST base-class resolver covers Name, Attribute (head + dotted-tail), forbidden stdlib, alias-as-module, and aliased-symbol cases; collapsing the branches would obscure each case
    node: ast.expr,
    alias_map: dict[str, tuple[str, str | None]],
    current_module: str,
) -> tuple[str, str] | None:
    """Resolve a base-class AST node to ``(module, name)`` or ``None``.

    Returns:
        ``(_FORBIDDEN_PSEUDO_MODULE, base_name)`` for forbidden stdlib
        names that aren't shadowed by a local import.
        ``(module_dotted, name)`` for cross-module references resolved
        through *alias_map*.
        ``(current_module, name)`` for local-module references with no
        matching import.
        ``None`` for unsupported expression forms (subscript, call, ...).

    Edge case: deeply chained attribute access through a module alias
    (``import x.y as z; class Foo(z.a.b.C):`` -> ``("x.y.a.b", "C")``)
    constructs a synthetic dotted module path that may not match an
    indexed entry. The closure pass treats unmatched bases as
    "non-rooted, non-forbidden" -- the right outcome: such a class
    surfaces as unresolved but is not flagged unless one of its other
    bases is also forbidden. The codebase doesn't use this shape; the
    branch exists for completeness and the index miss is the safe
    default.
    """
    if isinstance(node, ast.Name):
        if node.id in alias_map:
            module, original = alias_map[node.id]
            if original is None:
                return None
            return (module, original)
        if node.id in FORBIDDEN_BASES:
            return (_FORBIDDEN_PSEUDO_MODULE, node.id)
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
        if original is None:
            if len(attrs) == 1:
                return (module, attrs[0])
            return (module + "." + ".".join(attrs[:-1]), attrs[-1])
        return None
    return None


# ── Scanning ─────────────────────────────────────────────────────


class _ClassEntry:
    """Indexed metadata for one ``class`` definition in the source tree."""

    __slots__ = ("bases", "lineno", "module", "name", "rel", "suppressed")

    def __init__(  # noqa: PLR0913 -- frozen dataclass-style init; keyword-only would clutter every callsite
        self,
        rel: str,
        lineno: int,
        module: str,
        name: str,
        bases: list[tuple[str, str] | None],
        *,
        suppressed: bool,
    ) -> None:
        self.rel = rel
        self.lineno = lineno
        self.module = module
        self.name = name
        self.bases = bases
        self.suppressed = suppressed


def _index_file(
    path: Path,
    rel: str,
) -> tuple[list[_ClassEntry], str | None]:
    """Return every class-definition entry in *path* (and parse error, if any)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"{rel}:0: unable to read file: {exc}"
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [], f"{rel}:{exc.lineno or 0}: unable to parse file: {exc.msg}"
    lines = text.splitlines()
    alias_map = _build_alias_map(tree)
    module = _module_dotted_for_rel(rel)
    entries: list[_ClassEntry] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [_resolve_base(b, alias_map, module) for b in node.bases]
        suppressed = _class_def_suppressed(node, lines)
        entries.append(
            _ClassEntry(
                rel=rel,
                lineno=node.lineno,
                module=module,
                name=node.name,
                bases=bases,
                suppressed=suppressed,
            )
        )
    return entries, None


def _class_def_suppressed(node: ast.ClassDef, lines: list[str]) -> bool:
    """Return True iff any line of the class header carries the marker.

    The class header spans from the ``class`` keyword line through the
    first statement of the body; the marker may sit on any of those
    lines. This mirrors check_persistence_boundary's two-line rule.
    """
    start = node.lineno
    end = node.body[0].lineno if node.body else start
    for lineno in range(start, end + 1):
        if 1 <= lineno <= len(lines) and _line_has_trailing_marker(lines[lineno - 1]):
            return True
    return False


def _compute_rooted(
    entries: list[_ClassEntry],
) -> set[tuple[str, str]]:
    """Return the closure of (module, class_name) pairs reaching DomainError."""
    rooted: set[tuple[str, str]] = {(_DOMAIN_ERROR_MODULE, _DOMAIN_ERROR_NAME)}
    by_key: dict[tuple[str, str], _ClassEntry] = {
        (e.module, e.name): e for e in entries
    }
    changed = True
    while changed:
        changed = False
        for key, entry in by_key.items():
            if key in rooted:
                continue
            for base in entry.bases:
                if base is None:
                    continue
                if base in rooted:
                    rooted.add(key)
                    changed = True
                    break
    return rooted


def _has_forbidden_direct_base(entry: _ClassEntry) -> bool:
    """Return True iff *entry* directly inherits a forbidden stdlib base."""
    return any(
        base is not None and base[0] == _FORBIDDEN_PSEUDO_MODULE for base in entry.bases
    )


def _format_baseline_entry(rel: str, lineno: int, class_name: str) -> str:
    """Return the canonical baseline-entry string."""
    return f"{rel}:{lineno}:{class_name}"


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` file under *abs_root* as ``(abs, rel)``."""
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{rel_root}/*.py"],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return [
            (p, p.relative_to(project_root).as_posix())
            for p in sorted(abs_root.rglob("*.py"))
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _scan_tree(  # noqa: C901 -- two-pass closure + violation collection + drift detection
    project_root: Path,
    scan_root: Path,
    baseline: set[str] | None = None,
) -> list[str]:
    """Run the full scan; return a sorted list of violation messages.

    Args:
        project_root: Repo root used to compute relative paths.
        scan_root: Directory under *project_root* to walk.
        baseline: When provided, suppresses entries whose
            ``path:lineno:class_name`` key matches. ``None`` means no
            suppression. An empty set is the same as ``None`` for
            scanning, but baseline drift is only reported when a
            non-empty set is supplied.
    """
    files = _git_tracked_python_files(scan_root, project_root)
    all_entries: list[_ClassEntry] = []
    parse_errors: list[str] = []
    for path, rel in files:
        entries, error = _index_file(path, rel)
        if error is not None:
            parse_errors.append(error)
            continue
        all_entries.extend(entries)
    rooted = _compute_rooted(all_entries)
    violations: list[tuple[str, int, str, str]] = []
    seen_keys: set[str] = set()
    for entry in all_entries:
        if entry.suppressed:
            continue
        if not _has_forbidden_direct_base(entry):
            continue
        if (entry.module, entry.name) in rooted:
            continue
        baseline_key = _format_baseline_entry(entry.rel, entry.lineno, entry.name)
        seen_keys.add(baseline_key)
        if baseline is not None and baseline_key in baseline:
            continue
        forbidden_names = sorted(
            base[1]
            for base in entry.bases
            if base is not None and base[0] == _FORBIDDEN_PSEUDO_MODULE
        )
        violations.append(
            (
                entry.rel,
                entry.lineno,
                entry.name,
                ", ".join(forbidden_names),
            )
        )
    messages = sorted(parse_errors)
    for rel, lineno, name, bases in sorted(violations):
        messages.append(
            f"{rel}:{lineno}: {name} inherits from plain "
            f"{bases}; should inherit from DomainError. "
            "See src/synthorg/core/domain_errors.py for the canonical "
            "pattern (or add '# lint-allow: domain-error-hierarchy "
            "-- <reason>' if the stdlib base is genuinely intentional)."
        )
    if baseline:
        for stale in sorted(baseline - seen_keys):
            messages.append(
                f"{stale}: stale baseline entry (no matching violation "
                "found; the class may have been migrated, renamed, or "
                "deleted). Regenerate the baseline with "
                "'uv run python scripts/check_domain_error_hierarchy.py "
                "--update-baseline' to drop drifted rows."
            )
    return messages


# ── Baseline I/O ─────────────────────────────────────────────────


def _load_baseline(baseline_path: Path) -> set[str]:
    """Return the set of allowlisted ``path:lineno:class_name`` entries.

    Validates each non-empty, non-comment line; rejects duplicates and
    malformed entries. A corrupted baseline silently dropping entries
    would let real violations slip past the gate, so the loader fails
    loud on validation errors.
    """
    if not baseline_path.exists():
        return set()
    entries: set[str] = set()
    errors: list[str] = []
    rel_path = str(baseline_path)
    for lineno, line in enumerate(
        baseline_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _BASELINE_ENTRY_PATTERN.match(stripped):
            errors.append(
                f"{rel_path}:{lineno}: malformed entry (expected "
                f"'path:lineno:class_name', got {stripped!r})"
            )
            continue
        if stripped in entries:
            errors.append(f"{rel_path}:{lineno}: duplicate entry {stripped!r}")
            continue
        entries.add(stripped)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        msg = (
            f"{rel_path}: baseline failed validation "
            f"({len(errors)} error{'s' if len(errors) != 1 else ''}); "
            "regenerate with 'uv run python "
            "scripts/check_domain_error_hierarchy.py --update-baseline' "
            "or fix by hand."
        )
        raise ValueError(msg)
    return entries


def _scan_for_baseline_entries(
    project_root: Path,
    scan_root: Path,
) -> list[str]:
    """Return every violation in the tree as baseline entries (sorted)."""
    files = _git_tracked_python_files(scan_root, project_root)
    all_entries: list[_ClassEntry] = []
    for path, rel in files:
        entries, _ = _index_file(path, rel)
        all_entries.extend(entries)
    rooted = _compute_rooted(all_entries)
    found: list[tuple[str, int, str]] = []
    for entry in all_entries:
        if entry.suppressed:
            continue
        if not _has_forbidden_direct_base(entry):
            continue
        if (entry.module, entry.name) in rooted:
            continue
        found.append((entry.rel, entry.lineno, entry.name))
    found.sort()
    return [_format_baseline_entry(rel, lineno, name) for rel, lineno, name in found]


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
        help=(
            "Root to scan (relative to repo root). The gate always walks "
            "the full tree under this path because cross-module base "
            "resolution requires it."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to anchor path resolution against. Defaults to "
            "the ancestor directory of this script."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Regenerate scripts/domain_error_hierarchy_baseline.txt from "
            "the current tree. Rare; requires explicit user approval."
        ),
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help=(
            "Ignore the baseline file; report every violation. Useful "
            "for measuring residual progress during the rollout."
        ),
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

    baseline_path = project_root / _BASELINE_REL_PATH

    if args.update_baseline:
        entries = _scan_for_baseline_entries(project_root, scan_root)
        body = _BASELINE_HEADER
        if entries:
            body += "\n".join(entries) + "\n"
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(body, encoding="utf-8")
        print(
            f"Wrote {len(entries)} entries to {baseline_path}.",
            file=sys.stderr,
        )
        return 0

    baseline: set[str] | None
    if args.no_baseline:
        baseline = None
    else:
        try:
            baseline = _load_baseline(baseline_path)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    messages = _scan_tree(project_root, scan_root, baseline=baseline)
    if messages:
        for msg in messages:
            print(msg)
        print(
            f"\n{len(messages)} domain-error-hierarchy violation(s) found. "
            "See docs/reference/errors.md for the DomainError contract.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
