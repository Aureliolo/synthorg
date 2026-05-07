#!/usr/bin/env python3
r"""Pre-push / CI gate: forbid migration / origin framing in code.

Origin framing inside committed FILES rots the moment the old thing
is gone -- future readers waste time decoding context that already
lives in git log + the merge commit body. This gate catches the
recurring shapes:

* ``ported from`` / ``previously called`` / ``renamed from`` /
  ``moved here in`` / ``we used to`` -- forensic prose that names a
  past state of the code.
* ``Phase \\d+`` and ``phase \\d+`` -- ordinal pipeline numbering
  couples to a specific shape; semantic names (``decompose``,
  ``route``, ``dispatch``) survive insertions / reorders.
* ``Round-\\d+ fix`` / ``round-\\d+ review`` -- round numbering is
  feedback-loop scaffolding that means nothing once a feature ships.

The gate scans ``*.py`` files under ``src/synthorg/`` and ``tests/``
plus ``*.sql`` under ``src/synthorg/persistence/``. Documentation
trees (``docs/design/``, ``docs/reference/``, ``CHANGELOG.md``) are
the canonical home for migration / phase narrative and are NEVER
scanned.

Per-line opt-out::

    PATTERN = "Phase 1"  # lint-allow: migration-framing -- gate self-fixture

The justification after ``--`` MUST be non-empty (whitespace-only is
rejected).

Usage::

    python scripts/check_no_migration_framing.py                # default scope
    python scripts/check_no_migration_framing.py --paths src/synthorg
    python scripts/check_no_migration_framing.py path/to/file.py  # pre-commit
"""

import argparse
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Final

# ── Forbidden patterns ─────────────────────────────────────────────

_FRAMING_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("ported from", re.compile(r"\bported from\b", re.IGNORECASE)),
    ("previously called", re.compile(r"\bpreviously called\b", re.IGNORECASE)),
    ("renamed from", re.compile(r"\brenamed from\b", re.IGNORECASE)),
    ("moved here in", re.compile(r"\bmoved here in\b", re.IGNORECASE)),
    ("we used to", re.compile(r"\bwe used to\b", re.IGNORECASE)),
    # ``Phase\s+\d+`` (any case). The lookbehind rejects identifier
    # contexts (``CoordinationPhaseResult`` -- the bare word ``Phase``
    # has no digit after, so the digit requirement already catches it,
    # but the lookbehind keeps belt+braces).
    (
        "Phase N",
        re.compile(r"(?<![A-Za-z0-9_])phase\s+\d+", re.IGNORECASE),
    ),
    # ``Round-\d+ fix`` / ``round-\d+ review`` narrative. The dashed
    # form is unambiguous (verb ``round`` never carries a dash).
    (
        "Round-N fix/review",
        re.compile(r"\bround-\d+\s+(?:fix|review|fix:|review:)", re.IGNORECASE),
    ),
)

_SUPPRESSION_MARKER: Final[str] = "lint-allow: migration-framing"

# ── Path scoping ───────────────────────────────────────────────────

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

_PATH_ALLOWLIST_PREFIXES: Final[tuple[str, ...]] = (
    "docs/design/",
    "docs/reference/",
    "_audit/",
    ".claude/",
    ".github/",
    # Atlas-managed migration revisions are auto-generated; their
    # hash is tracked in atlas.sum, so any hand-edit (including
    # comment scrubs) corrupts migration state. Skip the directory
    # entirely.
    "src/synthorg/persistence/postgres/revisions/",
    "src/synthorg/persistence/sqlite/revisions/",
    # Other gates' self-tests legitimately embed phrases like
    # "We used to hardcode" or "Phase N" as fixture data demonstrating
    # what the OTHER gate does or does not flag. Wholesale-exempt the
    # scripts test directory so every gate's tests stay independent.
    "tests/unit/scripts/test_check_",
)

_PATH_ALLOWLIST_FILES: Final[frozenset[str]] = frozenset(
    {
        "CHANGELOG.md",
        ".github/CHANGELOG.md",
        "scripts/check_no_review_origin_in_code.py",
        "scripts/check_no_migration_framing.py",
        "tests/unit/scripts/test_check_no_review_origin_in_code.py",
        "tests/unit/scripts/test_check_no_migration_framing.py",
    }
)

_SCANNED_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".sql"})

_DEFAULT_ROOTS: Final[tuple[str, ...]] = ("src/synthorg", "tests")


# ── Suppression-marker detection ───────────────────────────────────


def _has_marker_with_reason(comment: str) -> bool:
    """Return True iff *comment* carries the marker with a non-empty reason.

    Accepts the marker anywhere in the comment text, followed by
    ``-- <reason>`` or ``: <reason>`` with a non-empty justification.
    Empty reasons (``-- `` / no separator) do NOT suppress.
    """
    idx = comment.find(_SUPPRESSION_MARKER)
    if idx == -1:
        return False
    tail = comment[idx + len(_SUPPRESSION_MARKER) :].lstrip()
    if not tail:
        return False
    if tail.startswith("--"):
        reason = tail[2:].strip()
        return bool(reason)
    if tail.startswith(":"):
        reason = tail[1:].strip()
        return bool(reason)
    return False


def _line_has_dedicated_marker(line: str) -> bool:
    """Return True iff *line* is a whole-line marker comment with a reason."""
    stripped = line.strip()
    if not stripped.startswith("#") and not stripped.startswith("--"):
        return False
    return _has_marker_with_reason(stripped)


def _line_has_trailing_marker_python(line: str) -> bool:
    """Return True iff a Python line carries the marker as a trailing ``#``."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        if _has_marker_with_reason(tok.string):
            return True
    return False


def _line_has_trailing_marker_sql(line: str) -> bool:
    """Return True iff a SQL line carries the trailing ``--`` marker."""
    idx = line.find("--")
    if idx == -1:
        return False
    return _has_marker_with_reason(line[idx:])


# ── Path resolution ────────────────────────────────────────────────


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Resolve *root* anchored under *project_root*; reject traversal."""
    candidate = root if root.is_absolute() else project_root / root
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return None
    return resolved


def _path_allowlisted(rel: str) -> bool:
    """Return True iff *rel* (POSIX) is in an allowlisted path."""
    if rel in _PATH_ALLOWLIST_FILES:
        return True
    return any(rel.startswith(prefix) for prefix in _PATH_ALLOWLIST_PREFIXES)


def _path_in_scope(rel: str) -> bool:
    """Return True iff *rel* is under a default-roots directory."""
    return any(rel == r or rel.startswith(r + "/") for r in _DEFAULT_ROOTS)


# ── Scanning ───────────────────────────────────────────────────────


def _scan_file(file_path: Path, rel: str) -> list[str]:
    """Return violation messages for *file_path*."""
    if not _path_in_scope(rel):
        return []
    if _path_allowlisted(rel):
        return []
    if file_path.suffix not in _SCANNED_SUFFIXES:
        return []
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:0: unable to scan file: {exc}"]
    issues: list[str] = []
    file_lines = text.splitlines()
    is_sql = file_path.suffix == ".sql"
    trailing_marker = (
        _line_has_trailing_marker_sql if is_sql else _line_has_trailing_marker_python
    )
    for idx, line in enumerate(file_lines, start=1):
        if trailing_marker(line):
            continue
        if idx > 1 and _line_has_dedicated_marker(file_lines[idx - 2]):
            continue
        for label, pattern in _FRAMING_PATTERNS:
            if pattern.search(line):
                issues.append(f"{rel}:{idx}: {label}: {line.rstrip()}")
    return issues


def _git_tracked_files(abs_root: Path, project_root: Path) -> list[tuple[Path, str]]:
    """Return every tracked in-scope file under *abs_root* as ``(abs, rel)``."""
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    patterns: list[str] = []
    for suffix in sorted(_SCANNED_SUFFIXES):
        patterns.append(f"{rel_root}/*{suffix}")
        patterns.append(f"{rel_root}/**/*{suffix}")
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", *patterns],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return [
            (p, p.relative_to(project_root).as_posix())
            for p in abs_root.rglob("*")
            if p.is_file() and p.suffix in _SCANNED_SUFFIXES
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p]
    seen: dict[str, tuple[Path, str]] = {}
    for rel_path in paths:
        if rel_path in seen:
            continue
        seen[rel_path] = ((project_root / rel_path), rel_path)
    return list(seen.values())


def _iter_targets(roots: list[Path], project_root: Path) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, posix_relative_path)`` for every file to scan."""
    targets: list[tuple[Path, str]] = []
    for root in roots:
        abs_root = _resolve_root(root, project_root)
        if abs_root is None or not abs_root.exists():
            continue
        for path, rel in _git_tracked_files(abs_root, project_root):
            if not _path_in_scope(rel):
                continue
            if _path_allowlisted(rel):
                continue
            targets.append((path, rel))
    return targets


def _scan_explicit_paths(paths: list[str], project_root: Path) -> tuple[list[str], int]:
    """Scan a set of file paths supplied on the CLI (pre-commit mode)."""
    violations: list[str] = []
    scanned = 0
    for raw in paths:
        abs_path = Path(raw).resolve()
        try:
            rel = abs_path.relative_to(project_root).as_posix()
        except ValueError:
            continue
        if abs_path.suffix not in _SCANNED_SUFFIXES:
            continue
        if not _path_in_scope(rel):
            continue
        if _path_allowlisted(rel):
            continue
        violations.extend(_scan_file(abs_path, rel))
        scanned += 1
    return violations, scanned


# ── CLI ───────────────────────────────────────────────────────────


def _force_utf8_streams() -> None:
    """Force UTF-8 on stdout/stderr to survive Windows ``cp1252`` consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    _force_utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Files to check (pre-commit supplies these). When omitted, "
            "the script scans every tracked file under --paths."
        ),
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        dest="roots",
        default=list(_DEFAULT_ROOTS),
        help="Roots to scan (relative to repo root).",
    )
    args = parser.parse_args(argv)

    project_root = _REPO_ROOT
    roots = [Path(p) for p in args.roots]
    for root in roots:
        if _resolve_root(root, project_root) is None:
            print(
                f"refusing to scan path outside project root: {root}",
                file=sys.stderr,
            )
            return 2

    if args.paths:
        violations, _ = _scan_explicit_paths(args.paths, project_root)
    else:
        violations = []
        for path, rel in _iter_targets(roots, project_root):
            violations.extend(_scan_file(path, rel))

    if not violations:
        return 0

    for line in violations:
        print(line)
    print(
        f"\n{len(violations)} migration-framing violation(s) found."
        " Origin framing inside committed files rots the moment the"
        " old thing is gone. Drop the historical narrative; describe"
        " current state only. Per-line opt-out:"
        " '# lint-allow: migration-framing -- <reason>' (mandatory"
        " non-empty justification).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
