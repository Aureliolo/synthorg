#!/usr/bin/env python3
"""Pre-push / CI gate: forbid review-origin and issue back-refs in code.

Code comments answer ONE question: *why is this code shaped this way,
that the next reader couldn't infer from the code itself?* Anything
else is rot. This gate enforces the rule for the categories that
silently drift the moment the review is resolved or the issue
renumbered:

* Reviewer citations: ``pre-PR review #N``, ``CodeRabbit at <file>:<line>``,
  ``Round-N`` / ``round-N`` narrative.
* In-code issue / PR back-references: ``(#NNNN)`` paren-form,
  ``Issue #N``, ``fixes #N``, ``closes #N``, ``see PR #N``,
  ``as part of #N``, ``GH-NNNN``.
* Naked ``SEC-N`` taxonomy in ``src/synthorg/`` or ``tests/`` -- the
  canonical home is ``docs/design/`` / ``docs/reference/``; appearing
  unexplained in code wastes the next reader's time.

The gate scans ``*.py`` files under ``src/synthorg/`` and ``tests/``
plus ``*.sql`` under ``src/synthorg/persistence/``. Documentation
trees (``docs/design/``, ``docs/reference/``, ``CHANGELOG.md``) are
the canonical home for these tokens and are NEVER scanned.

Per-line opt-out::

    something = "pre-PR review #N"  # lint-allow: review-origin -- legacy fixture used by the parser tests

The justification after ``--`` MUST be non-empty (whitespace-only is
rejected); empty markers do not suppress the gate. This forces the
reader to record *why* the line is exempt instead of papering over
violations.

Usage::

    python scripts/check_no_review_origin_in_code.py                # default scope
    python scripts/check_no_review_origin_in_code.py --paths src/synthorg
    python scripts/check_no_review_origin_in_code.py path/to/file.py  # pre-commit
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

# Reviewer-origin citations.
_REVIEWER_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("pre-PR review", re.compile(r"pre-PR review\s*#\d+", re.IGNORECASE)),
    ("CodeRabbit", re.compile(r"\bCodeRabbit\b")),
    ("Round-N", re.compile(r"\bRound-\d+\b")),
    ("round-N", re.compile(r"\bround-\d+\b")),
    # Bare ``Round N`` narrative is case-sensitive on the capital ``R``
    # so the verb ``round`` (e.g. ``would round 0.5 down``) does not
    # trip the gate. Lowercase ``round N`` narrative is too ambiguous
    # to flag automatically; the cleanup pass handles those manually.
    ("Round N narrative", re.compile(r"\bRound\s+\d+\b")),
)

# In-code issue / PR back-refs.
_BACKREF_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # Paren-form (#NNNN) where N is at least 3 digits to skip placeholders.
    ("(#NNNN)", re.compile(r"\(#\d{3,}\b")),
    # Narrative back-refs that name an issue / PR by number.
    (
        "narrative #N",
        re.compile(
            r"\b(?:issue|see|fixes|fix|closes|close|under|until|once|"
            r"see\s+PR|part\s+of|via|tracked\s+by)\s+#\d{2,}",
            re.IGNORECASE,
        ),
    ),
    # GitHub global-id form.
    ("GH-N", re.compile(r"\bGH-\d{3,}\b")),
)

# Naked SEC-N taxonomy. Word boundaries reject ``SEC-1`` inside
# longer identifiers (none should exist) and the word ``records``.
_SEC_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bSEC-\d+\b")

# Suppression marker -- requires non-empty justification.
_SUPPRESSION_MARKER: Final[str] = "lint-allow: review-origin"

# ── Path scoping ───────────────────────────────────────────────────

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Files / globs whose payload IS the forbidden token (the rule's
# canonical home or the gate's own self-test fixtures). The gate
# does not read their contents at all.
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
    # ``# pre-PR review #N`` or ``(#1234)`` as fixture data
    # demonstrating what the OTHER gate does or does not flag.
    # Wholesale-exempt the scripts test directory so every gate's
    # tests stay independent.
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

# In-scope file suffixes. SQL is included so persistence-revision
# header comments get caught.
_SCANNED_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".sql"})

# Roots whose tree is in scope. Anything outside is silently skipped
# -- the gate is opt-in by directory.
_DEFAULT_ROOTS: Final[tuple[str, ...]] = ("src/synthorg", "tests")


# ── Suppression-marker detection ───────────────────────────────────


def _has_marker_with_reason(comment: str) -> bool:
    """Return True iff *comment* carries the marker with a non-empty reason.

    Accepts the marker anywhere inside the comment text, followed by
    one of two separators and a non-empty justification:

    * ``lint-allow: review-origin -- <reason>`` (preferred)
    * ``lint-allow: review-origin: <reason>`` (alternate sep)

    The justification text after the separator must be non-empty
    *after* whitespace strip. ``lint-allow: review-origin --`` and
    bare ``lint-allow: review-origin`` do NOT suppress -- the rule
    forces the reader to record *why*.
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
    """Return True iff *line* is exactly the marker comment.

    Whole-line markers must contain ONLY the marker (and a reason).
    A marker embedded in prose -- ``# TODO lint-allow: review-origin
    -- later`` -- is rejected so it cannot bleed forward into the next
    line. Reused from the equivalent guard in
    ``check_forbidden_literals.py``.
    """
    stripped = line.strip()
    if not stripped.startswith("#") and not stripped.startswith("--"):
        return False
    return _has_marker_with_reason(stripped)


def _line_has_trailing_marker_python(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing ``#`` comment.

    Uses :mod:`tokenize` so a ``#`` inside a string literal is not
    mistaken for a comment, mirroring ``check_forbidden_literals.py``.
    Falls back to ``False`` (fail-closed) on parse errors.
    """
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
    """Return True iff a SQL line carries the trailing ``--`` marker.

    SQL comments use ``--``. We do not try to tokenize SQL; a string
    literal containing ``--`` is unusual enough that the simple split
    is good enough for migration-header use.
    """
    idx = line.find("--")
    if idx == -1:
        return False
    comment = line[idx:]
    return _has_marker_with_reason(comment)


# ── Path resolution ────────────────────────────────────────────────


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Resolve *root* anchored under *project_root*; reject traversal.

    Returns ``None`` on traversal so the caller can treat it as a
    fatal argv error rather than silently skip. Mirrors
    ``check_forbidden_literals.py``.
    """
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


def _all_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Return every (label, regex) pair the gate enforces."""
    return (
        *_REVIEWER_PATTERNS,
        *_BACKREF_PATTERNS,
        ("SEC-N taxonomy", _SEC_PATTERN),
    )


def _scan_file(file_path: Path, rel: str) -> list[str]:
    """Return violation messages for *file_path* (rel-keyed for output).

    *rel* is the POSIX-style path used in the violation message; the
    caller chooses the anchor (project root for production, tmp root
    for tests). Out-of-scope files (anything outside ``src/synthorg/``
    or ``tests/``) and allowlisted paths short-circuit to ``[]`` so
    the function is safe to call directly from unit tests.
    """
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
        for label, pattern in _all_patterns():
            if pattern.search(line):
                issues.append(f"{rel}:{idx}: {label}: {line.rstrip()}")
                # Continue checking other patterns on the same line so
                # a multi-issue line surfaces every label.
    return issues


def _git_tracked_files(abs_root: Path, project_root: Path) -> list[tuple[Path, str]]:
    """Return every tracked in-scope file under *abs_root* as ``(abs, rel)``.

    Delegates to ``git ls-files`` so untracked scratch files are
    skipped. Falls back to ``rglob`` outside a git checkout (CI source
    tarballs) so the script still runs.
    """
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
    # ``git ls-files`` with ``**`` glob patterns can yield duplicates;
    # dedupe via a dict keyed on the relative path.
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
    """Scan a set of file paths supplied on the CLI (pre-commit mode).

    Returns ``(violations, scan_count)``. Files outside the repo or
    outside the in-scope roots are silently skipped, matching the
    convention of other pre-commit gates.
    """
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
    """Force UTF-8 on stdout/stderr to survive Windows ``cp1252`` consoles.

    Many violation lines include box-drawing characters from section
    headers; the default Windows console encoding crashes on them.
    """
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
        f"\n{len(violations)} review-origin / back-ref violation(s) found."
        " Code comments answer WHY only -- never reviewer citations,"
        " never issue back-refs. Origin context lives in git log + PR"
        " body, NEVER in committed files. Per-line opt-out:"
        " '# lint-allow: review-origin -- <reason>' (mandatory non-empty"
        " justification).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
