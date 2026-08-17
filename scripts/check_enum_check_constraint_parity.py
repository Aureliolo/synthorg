#!/usr/bin/env python3
"""Pre-push / CI gate: a persisted enum and its CHECK admit the same set.

A ``StrEnum`` whose members are written into a column guarded by
``CHECK (col IN ('a', 'b', ...))`` has its vocabulary declared twice: once in
Python and once per backend in SQL. Nothing kept the two in step, and they
drifted in the direction that fails silently.

``BlockedReason.NO_CAPABLE_AGENT`` shipped, was written by two production
paths (``engine/coordination/service.py`` when routing finds nobody, and
``engine/review_staffing/unroutable.py``), and appeared in neither backend's
CHECK. So a subtask nobody could take was never parked at all: the write
violated the constraint, the row stayed in whatever status it already held,
and a live run ended with two subtasks sitting at ``created``, undispatched,
with nothing watching them and no exit. The park exists to prevent exactly
that state and could not be recorded.

``check_schema_drift.py`` does not see this. It compares ``schema.sql``
against the accumulated revisions, and both agreed, because both were wrong
together. The question this gate asks is the one neither of them asks: does
the column admit what the enum can produce?

Detection
---------
Parse each backend's ``schema.sql`` for every CHECK whose ENTIRE body is one
column-vocabulary predicate::

    CHECK (col IN ('a', 'b', ...))
    CHECK (col IS NULL OR col IN ('a', 'b', ...))

and collect the admitted set. AST-walk every tracked ``*.py`` under
``src/synthorg/`` for ``StrEnum`` subclasses and collect each one's member
values. A CHECK set that matches no enum exactly but is a STRICT SUBSET of
one is reported, naming the missing members.

The whole-body restriction is what separates a vocabulary from an invariant.
``CHECK ((status = 'pending' AND decided_at IS NULL) OR (status IN
('approved', 'rejected') AND decided_at IS NOT NULL))`` names a subset on
purpose: it is a per-branch consistency rule, not a claim about what the
column may hold. Reading those as vocabularies produced eleven findings that
were all correct SQL, which would have buried the one real defect.

An exact match to some enum passes even when a larger enum also contains the
set. ``risk_level IN ('low', 'medium', 'high', 'critical')`` is ``RiskLevel``
in full; that it is also a subset of ``RedTeamSeverity`` says nothing.

Subset, not inequality, is the test on purpose. A CHECK admitting a value no
enum declares is a different defect (dead vocabulary in SQL), it fails no
write, and flagging it here would bury the one that does.

Sets smaller than two literals are skipped: a one-value CHECK is a constant,
not a vocabulary, and matching it against every enum containing that string
is noise.

What it does NOT do
-------------------
It does not know which column belongs to which enum, and deliberately does
not try: a declared mapping is one rename away from disagreeing with the
schema it claims to describe. Matching by value set is exact for the drift
that matters, because the CHECK was written by copying the enum.

It says nothing about conditional-invariant CHECKs, per above, and nothing
about a ``NOT IN`` exclusion list, which is the complement of a vocabulary
rather than one.

Allowlist / opt-out
-------------------
A column that deliberately admits a subset of an enum puts
``-- lint-allow: enum-check-parity -- <reason>`` on any line of the CHECK's
own statement. The justification after ``--`` is required, because a narrowed
vocabulary is a decision and this is the only place it gets written down.

There is deliberately no baseline: the tree is clean as of this gate, and a
persisted value the database refuses is never something to preserve.

Usage::

    uv run python scripts/check_enum_check_constraint_parity.py

Exit codes:
    0 -- every CHECK admits its enum's full vocabulary.
    1 -- a CHECK admits strictly less than the enum that fills it.
    2 -- configuration error (bad ``--repo-root``, or a source file that could
         not be read or parsed -- fail-closed).
"""

import argparse
import ast
import re
import subprocess
import sys
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
_SCHEMA_RELS: Final[tuple[str, ...]] = (
    "src/synthorg/persistence/sqlite/schema.sql",
    "src/synthorg/persistence/postgres/schema.sql",
)
_SUPPRESSION_MARKER: Final[str] = "lint-allow: enum-check-parity"

#: A one-literal CHECK is a constant, not a vocabulary.
_MIN_VOCABULARY: Final[int] = 2

#: A CHECK whose whole body is one column-vocabulary predicate, with an
#: optional ``IS NULL OR`` guard naming the same column. Anything else in the
#: body makes it an invariant rather than a vocabulary; see the module
#: docstring.
_VOCABULARY_BODY: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?P<guard>\w+)\s+IS\s+NULL\s+OR\s+)?"
    r"(?P<column>\w+)\s+IN\s*\((?P<values>[^()]*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_SQL_LITERAL: Final[re.Pattern[str]] = re.compile(r"'([^']*)'")
_CHECK_KEYWORD: Final[re.Pattern[str]] = re.compile(r"\bCHECK\s*\(", re.IGNORECASE)

#: Base classes whose subclasses carry a persisted string vocabulary.
_ENUM_BASES: Final[frozenset[str]] = frozenset({"StrEnum"})


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _EnumVocabulary:
    """One ``StrEnum`` and the values its members declare."""

    rel: str
    name: str
    values: frozenset[str]


@dataclass(frozen=True)
class _CheckSet:
    """One ``CHECK (col IN (...))`` and the values it admits."""

    rel: str
    lineno: int
    column: str
    values: frozenset[str]


@dataclass(frozen=True)
class _Hit:
    """A CHECK admitting strictly less than the enum that fills it."""

    check: _CheckSet
    enum: _EnumVocabulary

    def message(self) -> str:
        """Return the human-facing violation message."""
        missing = ", ".join(sorted(self.enum.values - self.check.values))
        return (
            f"{self.check.rel}:{self.check.lineno}: CHECK on "
            f"'{self.check.column}' admits {len(self.check.values)} of "
            f"{self.enum.name}'s {len(self.enum.values)} values; writing "
            f"{missing} violates the constraint and the row keeps its old "
            f"status. Add the missing value(s) here and in a new revision "
            f"for every backend ({self.enum.rel} declares them)."
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
            f"check_enum_check_constraint_parity: git ls-files failed in "
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


def _is_str_enum(node: ast.ClassDef) -> bool:
    """Return True iff *node* declares a ``StrEnum`` subclass.

    Returns:
        ``True`` when any base resolves to a name in :data:`_ENUM_BASES`.
    """
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in _ENUM_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in _ENUM_BASES:
            return True
    return False


def _member_values(node: ast.ClassDef) -> frozenset[str]:
    """Return the string values the class's members assign.

    Returns:
        Every ``NAME = "value"`` right-hand side in the class body.
    """
    values: set[str] = set()
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = stmt.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.add(value.value)
    return frozenset(values)


def _collect_enums(project_root: Path) -> list[_EnumVocabulary]:
    """Return every ``StrEnum`` vocabulary declared under ``src/synthorg``.

    Returns:
        One :class:`_EnumVocabulary` per enum with at least two members.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    found: list[_EnumVocabulary] = []
    for path, rel in _git_tracked_python_files(abs_root, project_root):
        _text, tree = read_and_parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_str_enum(node):
                continue
            values = _member_values(node)
            if len(values) >= _MIN_VOCABULARY:
                found.append(_EnumVocabulary(rel=rel, name=node.name, values=values))
    return found


def _is_valid_marker(line: str) -> bool:
    """Return True iff *line* carries a justified suppression marker.

    Returns:
        ``True`` for ``-- lint-allow: enum-check-parity -- <reason>``.
    """
    if _SUPPRESSION_MARKER not in line:
        return False
    suffix = line.split(_SUPPRESSION_MARKER, 1)[1].strip()
    return suffix.startswith("--") and bool(suffix[2:].strip())


def _ends_statement(line: str) -> bool:
    """Return True iff *line* terminates a SQL statement.

    The comment is stripped first: this schema's prose routinely ends a
    ``--`` line with a semicolon, and reading that as a terminator cut the
    suppression scope short of the very marker it was meant to carry.

    Returns:
        ``True`` when the line's code, comment removed, ends in ``;``.
    """
    return line.split("--", 1)[0].rstrip().endswith(";")


def _statement_span(lines: list[str], start_index: int) -> tuple[int, int]:
    """Return the 0-indexed line range of the statement containing a match.

    A CHECK constraint spans several lines and its marker may sit on any of
    them, so the whole statement is the suppression scope.

    Returns:
        ``(first, last)`` inclusive 0-indexed line numbers.
    """
    first = start_index
    while first > 0 and not _ends_statement(lines[first - 1]):
        first -= 1
    last = start_index
    while last < len(lines) - 1 and not _ends_statement(lines[last]):
        last += 1
    return first, last


def _check_bodies(text: str) -> list[tuple[int, str]]:
    """Return each ``CHECK (...)`` body with the 1-indexed line it opens on.

    Paren-matched rather than regex-bounded, so a body containing nested
    parentheses (a function call, an inner predicate) is returned whole and
    then rejected by the vocabulary shape rather than truncated into one.

    Returns:
        ``(lineno, body)`` pairs in source order.
    """
    bodies: list[tuple[int, str]] = []
    for match in _CHECK_KEYWORD.finditer(text):
        start = match.end()
        depth = 1
        index = start
        while index < len(text) and depth > 0:
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
            index += 1
        if depth != 0:
            continue
        bodies.append((text.count("\n", 0, match.start()) + 1, text[start : index - 1]))
    return bodies


def _collect_checks(project_root: Path) -> list[_CheckSet]:
    """Return every column-vocabulary CHECK in the declared schemas.

    Returns:
        One :class:`_CheckSet` per unsuppressed vocabulary CHECK with at
        least two literals.

    Raises:
        GateSourceError: If a schema file is missing or unreadable.
    """
    checks: list[_CheckSet] = []
    for rel in _SCHEMA_RELS:
        path = project_root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"{rel}: could not read declared schema: {exc}"
            raise GateSourceError(msg) from exc
        lines = text.splitlines()
        for lineno, body in _check_bodies(text):
            shape = _VOCABULARY_BODY.match(body)
            if shape is None:
                continue
            column = shape.group("column")
            guard = shape.group("guard")
            if guard is not None and guard.lower() != column.lower():
                continue
            literals = frozenset(_SQL_LITERAL.findall(shape.group("values")))
            if len(literals) < _MIN_VOCABULARY:
                continue
            first, last = _statement_span(lines, lineno - 1)
            if any(_is_valid_marker(line) for line in lines[first : last + 1]):
                continue
            checks.append(
                _CheckSet(rel=rel, lineno=lineno, column=column, values=literals)
            )
    return checks


def _best_superset(
    check: _CheckSet, enums: list[_EnumVocabulary]
) -> _EnumVocabulary | None:
    """Return the enum that most tightly contains *check*'s values.

    An exact match to any enum means the column admits that vocabulary in
    full, so nothing is reported even when a larger enum also contains the
    set: ``risk_level IN ('low', 'medium', 'high', 'critical')`` is
    ``RiskLevel`` entire, and its being a subset of ``RedTeamSeverity`` is a
    coincidence of shared words.

    Returns:
        The strict superset with the fewest extra members, or ``None`` when
        some enum matches exactly or none contains the admitted set.
    """
    if any(check.values == e.values for e in enums):
        return None
    candidates = [e for e in enums if check.values < e.values]
    if not candidates:
        return None
    return min(candidates, key=lambda e: (len(e.values), e.name))


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
        enums = _collect_enums(project_root)
        checks = _collect_checks(project_root)
    except GateSourceError as exc:
        print(f"check_enum_check_constraint_parity: {exc}", file=sys.stderr)
        return 2

    hits = [
        _Hit(check=check, enum=enum)
        for check in checks
        if (enum := _best_superset(check, enums)) is not None
    ]
    if not hits:
        return 0
    hits.sort(key=lambda h: (h.check.rel, h.check.lineno))
    for hit in hits:
        print(hit.message())
    print(
        f"\n{len(hits)} CHECK constraint(s) admit less than the enum that "
        "fills them. A value the column refuses is a write that fails and a "
        "row that keeps its old status. Add the missing value(s) to both "
        "schema.sql files and one new revision per backend, or add "
        "'-- lint-allow: enum-check-parity -- <reason>' inside the CHECK's "
        "own statement when the narrowing is deliberate.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
