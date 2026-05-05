"""Pre-push / CI dependency-inversion gate.

High-level packages (``engine/``, ``api/``, ``communication/``) MUST
NOT import concrete persistence classes when a Protocol exists in the
same domain. Examples of forbidden imports:

- ``from synthorg.persistence.config import SQLiteConfig`` outside the
  factory module -- callers must accept ``PersistenceConfig`` and let
  ``persistence/config_factory.py`` build the dialect-specific shape.
- ``from synthorg.persistence.filesystem_artifact_storage import
  FileSystemArtifactStorage`` -- callers depend on
  ``ArtifactStorageBackend``.
- ``from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend``
  -- callers depend on ``PersistenceBackend``.
- ``from synthorg.persistence.postgres.escalation_repo import
  PostgresEscalationRepository`` -- callers depend on the
  ``EscalationQueueStore`` Protocol or the
  ``CrossInstanceNotifyCapableStore`` capability marker.

Sanctioned exceptions are factory modules (``persistence/factory.py``,
``persistence/config_factory.py``, ``ontology/versioning.py``) that
exist precisely to centralise the dialect choice; the gate
allowlists them by name.

Per-line opt-out: ``# lint-allow: dependency-inversion -- <required
justification>`` as a trailing comment on the import line.

Exits non-zero with a structured violation list. Designed to run
under ``pre-push`` alongside ``check_persistence_boundary.py``.

Usage:
    python scripts/check_dependency_inversion.py
    python scripts/check_dependency_inversion.py --paths src/synthorg
"""

import argparse
import ast
import io
import os
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Final

# ── Allowlist (file paths exempt from the gate) ─────────────────

# Files whose entire purpose is to bridge Protocol -> concrete. They
# legitimately import the concretes and return the Protocol shape.
_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "src/synthorg/persistence/factory.py",
        "src/synthorg/persistence/config_factory.py",
        "src/synthorg/ontology/versioning.py",
    }
)

# Roots whose modules are subject to the gate. The persistence package
# itself is exempt -- it owns the concretes by design.
_SCAN_PREFIXES: Final[tuple[str, ...]] = (
    "src/synthorg/api/",
    "src/synthorg/engine/",
    "src/synthorg/communication/",
)

# Concrete persistence-class names whose import outside the persistence/
# package indicates a layering leak. Each entry maps to the suggested
# Protocol the importer should depend on instead.
_FORBIDDEN_IMPORTS: Final[dict[str, str]] = {
    # Backend configs -- callers should accept PersistenceConfig.
    "SQLiteConfig": "PersistenceConfig (via persistence.config_factory)",
    "PostgresConfig": "PersistenceConfig (via persistence.config_factory)",
    "PostgresSslMode": (
        "persistence.config_factory.resolve_postgres_ssl_mode_from_env()"
    ),
    # Concrete backends -- callers should depend on PersistenceBackend.
    "SQLitePersistenceBackend": "PersistenceBackend",
    "PostgresPersistenceBackend": "PersistenceBackend",
    # Filesystem artifact storage -- callers should depend on
    # ArtifactStorageBackend.
    "FileSystemArtifactStorage": "ArtifactStorageBackend",
    # Concrete escalation repository -- callers should rely on
    # EscalationQueueStore + CrossInstanceNotifyCapableStore.
    "PostgresEscalationRepository": (
        "EscalationQueueStore + CrossInstanceNotifyCapableStore"
    ),
    "SQLiteEscalationRepository": "EscalationQueueStore",
}

_SUPPRESSION_MARKER: Final[str] = "lint-allow: dependency-inversion"

# Modules that own the forbidden concretes. ``import synthorg.persistence.config``
# followed by ``synthorg.persistence.config.SQLiteConfig`` reaches the same
# concrete dependency the ``from ... import SQLiteConfig`` shape does, so the
# attribute-access pattern needs the same gate coverage as the from-import.
_FORBIDDEN_MODULE_PATHS: Final[dict[str, str]] = {
    "synthorg.persistence.config": (
        "PersistenceConfig (via persistence.config_factory)"
    ),
    "synthorg.persistence.filesystem_artifact_storage": "ArtifactStorageBackend",
    "synthorg.persistence.sqlite.backend": "PersistenceBackend",
    "synthorg.persistence.postgres.backend": "PersistenceBackend",
    "synthorg.persistence.postgres.escalation_repo": (
        "EscalationQueueStore + CrossInstanceNotifyCapableStore"
    ),
    "synthorg.persistence.sqlite.escalation_repo": "EscalationQueueStore",
}


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing comment.

    Marker syntax: ``# lint-allow: dependency-inversion -- <reason>``.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False


def _collect_module_aliases(
    tree: ast.Module,
    lines: list[str],
) -> dict[str, str]:
    """Return ``{access_path: forbidden_module}`` for forbidden module imports.

    Two import shapes need coverage so the attribute-access pass can
    resolve both:

    - ``import synthorg.persistence.config as cfg`` -- usage is
      ``cfg.SQLiteConfig``; the access base is the bare alias ``cfg``,
      so the key is ``"cfg"``.
    - ``import synthorg.persistence.config`` -- usage is
      ``synthorg.persistence.config.SQLiteConfig``; the access base
      walks the full dotted module path, so the key is the full module
      string (NOT just the root binding ``synthorg``: that wouldn't
      match the resolved attribute chain and would also collide with
      unrelated ``synthorg.X`` imports). The map value is the
      forbidden module the access reaches in either shape.

    Suppression markers on the import line skip the alias entirely so
    callers don't get a violation against an explicitly-allowed import.
    """
    aliases_to_module: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            module_name = alias.name
            if module_name not in _FORBIDDEN_MODULE_PATHS:
                continue
            lineno = node.lineno
            line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
            if _line_has_trailing_marker(line):
                continue
            access_key = alias.asname or module_name
            aliases_to_module[access_key] = module_name
    return aliases_to_module


def _check_import_from(
    node: ast.ImportFrom,
    rel: str,
    lines: list[str],
) -> list[str]:
    """Return violations for forbidden ``from ... import <Concrete>`` shapes."""
    issues: list[str] = []
    for alias in node.names:
        name = alias.name
        if name not in _FORBIDDEN_IMPORTS:
            continue
        lineno = node.lineno
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if _line_has_trailing_marker(line):
            continue
        issues.append(
            f"{rel}:{lineno}: imports concrete persistence "
            f"type {name!r}; depend on "
            f"{_FORBIDDEN_IMPORTS[name]} instead, or add "
            f"'# lint-allow: dependency-inversion -- <reason>' "
            f"on the import line."
        )
    return issues


def _check_attribute_access(
    node: ast.Attribute,
    rel: str,
    lines: list[str],
    aliases_to_module: dict[str, str],
) -> str | None:
    """Return a violation message for ``aliased_module.<Concrete>`` access.

    Resolves the attribute base back to a previously-imported module via
    *aliases_to_module*; non-resolvable bases (function calls, subscripts,
    locals shadowing an alias) yield ``None``.
    """
    attr_name = node.attr
    if attr_name not in _FORBIDDEN_IMPORTS:
        return None
    dotted = _resolve_dotted_module(node.value)
    if dotted is None:
        return None
    forbidden_module = aliases_to_module.get(dotted)
    if forbidden_module is None:
        return None
    lineno = node.lineno
    line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    if _line_has_trailing_marker(line):
        return None
    return (
        f"{rel}:{lineno}: references concrete persistence "
        f"type {attr_name!r} via attribute access on "
        f"{forbidden_module!r}; depend on "
        f"{_FORBIDDEN_IMPORTS[attr_name]} instead, or add "
        f"'# lint-allow: dependency-inversion -- <reason>' "
        f"on the line."
    )


def _scan_file(file_path: Path, rel: str) -> list[str]:
    """Return violation messages for a single file."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:0: unable to scan file: {exc}"]
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno or 0}: unable to parse file: {exc.msg}"]
    lines = text.splitlines()
    aliases_to_module = _collect_module_aliases(tree, lines)
    issues: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            issues.extend(_check_import_from(node, rel, lines))
        elif isinstance(node, ast.Attribute):
            msg = _check_attribute_access(node, rel, lines, aliases_to_module)
            if msg is not None:
                issues.append(msg)
    return issues


def _resolve_dotted_module(node: ast.expr) -> str | None:
    """Return the dotted module path bound to *node*, or ``None``.

    Walks an ``ast.Attribute`` chain back to its ``ast.Name`` root,
    rebuilding the dotted form (``synthorg.persistence.config``).
    Returns ``None`` for any expression shape that is not a pure
    name / attribute access (e.g. function calls, subscripts).
    """
    parts: list[str] = []
    current: ast.expr | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Resolve *root* to an absolute path strictly under *project_root*.

    Uses ``strict=True`` so symlinks pointing outside the project root
    are rejected at resolve time rather than slipping past the
    ``commonpath`` containment check (a symlink target is followed
    silently with ``strict=False``). The ``commonpath`` check stays as
    defence-in-depth in case ``strict=True`` is ever relaxed.
    """
    candidate = root if root.is_absolute() else project_root / root
    try:
        resolved = candidate.resolve(strict=True)
    except OSError, RuntimeError:
        # ``RuntimeError`` is what ``Path.resolve(strict=True)`` raises
        # on a symlink loop; ``OSError`` covers missing paths and
        # permission errors.
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


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` file under *abs_root* as ``(abs, rel)``."""
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    # ``git ls-files`` walks UP from cwd to find the enclosing repo. When
    # ``project_root`` is a synthetic tmp_path (test fixtures, ad-hoc
    # scans), git would find the wrapping project's repo and return its
    # tracked files instead of the synthetic ones. Skip the subprocess
    # whenever ``project_root`` is not itself a git work tree -- the
    # filesystem ``rglob`` fallback returns the right shape.
    if not (project_root / ".git").exists():
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{rel_root}/*.py"],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _is_in_scope(rel: str) -> bool:
    """Return True iff *rel* is a high-level module subject to the gate."""
    return any(rel.startswith(prefix) for prefix in _SCAN_PREFIXES)


def _iter_targets(
    roots: list[Path],
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, posix_relative_path)`` for every file to scan."""
    targets: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for root in roots:
        abs_root = _resolve_root(root, project_root)
        if abs_root is None or not abs_root.exists():
            continue
        for path, rel in _git_tracked_python_files(abs_root, project_root):
            if rel in seen:
                continue
            if not _is_in_scope(rel):
                continue
            if rel in _ALLOWLIST:
                continue
            seen.add(rel)
            targets.append((path, rel))
    return targets


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


def _scan_all(roots: list[Path], project_root: Path) -> int:
    """Run the import scan and print violations; returns the count."""
    total = 0
    for path, rel in _iter_targets(roots, project_root):
        issues = _scan_file(path, rel)
        for msg in issues:
            print(msg)
        total += len(issues)
    return total


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["src/synthorg"],
        help="Roots to scan (relative to repo root).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root anchor (defaults to this script's parent directory).",
    )
    args = parser.parse_args()

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    roots = [Path(p) for p in args.paths]
    for root in roots:
        if _resolve_root(root, project_root) is None:
            print(
                f"refusing to scan path outside project root: {root}",
                file=sys.stderr,
            )
            return 2

    total = _scan_all(roots, project_root)
    if total:
        print(
            f"\n{total} dependency-inversion violation(s) found. "
            "See docs/reference/persistence-boundary.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ProjectRootError",
    "_iter_targets",
    "_line_has_trailing_marker",
    "_resolve_dotted_module",
    "_resolve_project_root",
    "_resolve_root",
    "_scan_all",
    "_scan_file",
    "main",
]
