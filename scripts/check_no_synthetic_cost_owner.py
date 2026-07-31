#!/usr/bin/env python3
"""Pre-push / CI gate: no fabricated owner id reaches a cost chokepoint.

``CostRecord.agent_id`` and ``CostRecord.task_id`` name rows that exist:
``task_id`` is a real foreign key into ``tasks``. Work the system does for
itself (memory embedding, context compaction, a chief-of-staff turn) belongs
to no agent and no task, and the honest value for that is ``None``.

Passing a made-up string instead is not a cosmetic slip. The id matches no
task row, the insert fails the foreign key, and the cost-recording path
swallows that failure as a WARNING, so the spend is silently dropped and the
budget under-reports by exactly that amount. That defect shipped once, from
roughly forty call sites each inventing an id such as
``"system:memory:embedding"``.

Three separate regex sweeps of the same tree produced three different answers
about how many call sites remained, because a literal can sit inside a
``NotBlankStr(...)`` wrapper split across lines, hide behind an ``or``
fallback, or arrive as a bare identifier. That is why this gate parses the
AST instead.

Detection
---------
AST-walk every tracked ``*.py`` under ``src/synthorg/`` and flag an
``agent_id=`` or ``task_id=`` keyword on a cost chokepoint whose value is
fabricated rather than derived:

* a string literal (``task_id="system:memory:rerank"``);
* an f-string (``task_id=f"compaction:{execution_id}"``);
* either of those wrapped in a ``NotBlankStr(...)`` call;
* an ``or`` fallback onto either (``agent_id=responder.agent_id or "system"``).

A value read from a variable, attribute, subscript, or call is derived, and
passes. ``None`` passes: it is the honest answer for unowned work.

What it does NOT do
-------------------
It does not check that a derived value names a row that exists; only the
database can know that. The runtime counterpart is the cost-recorder's
consecutive-failure escalation, which surfaces a persistently rejected insert
instead of logging it once per call and moving on.

Allowlist / opt-out
-------------------
Per-line opt-out: append ``# lint-allow: synthetic-cost-owner -- <reason>`` to
the line carrying the keyword. The justification after ``--`` is required.

There is deliberately no baseline file. The tree is clean as of this gate's
introduction, and an owner id is either real or invented; a baseline would
only preserve invented ones.

Usage::

    uv run python scripts/check_no_synthetic_cost_owner.py

Exit codes:
    0 -- no fabricated owner ids.
    1 -- a fabricated owner id reaches a cost chokepoint.
    2 -- configuration error (bad ``--repo-root``, or a source file that could
         not be read, parsed, or tokenised -- fail-closed).
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
_SCAN_ROOT_REL: Final[str] = "src/synthorg"
_SUPPRESSION_MARKER: Final[str] = "lint-allow: synthetic-cost-owner"

# The chokepoints that stamp an owner onto a CostRecord. ``.record()`` is not
# listed: it takes an already-built CostRecord, so the construction site above
# is where a fabricated id enters, and that site is covered.
_TARGET_CALLS: Final[frozenset[str]] = frozenset(
    {
        "cost_recording_scope",
        "CostRecord",
        "complete_text",
        "complete_structured_text",
    }
)

_OWNER_KEYWORDS: Final[frozenset[str]] = frozenset({"agent_id", "task_id"})

# NotBlankStr("literal") is still a literal; the wrapper only narrows the type.
_TRANSPARENT_WRAPPERS: Final[frozenset[str]] = frozenset({"NotBlankStr"})


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One fabricated owner id passed to a cost chokepoint."""

    rel: str
    lineno: int
    col: int
    keyword: str
    call: str

    def message(self) -> str:
        """Return the human-facing violation message."""
        return (
            f"{self.rel}:{self.lineno}:{self.col}: {self.call}() is passed a "
            f"fabricated '{self.keyword}='. It names no row, so the insert "
            f"fails the foreign key and the spend is dropped. Pass the real "
            f"id, or None when the work has no owner."
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
            f"check_no_synthetic_cost_owner: git ls-files failed in "
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
        ``True`` for ``# lint-allow: synthetic-cost-owner -- <reason>``.
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


def _is_fabricated(value: ast.expr) -> bool:
    """Return True iff *value* is an invented id rather than a derived one.

    Unwraps the transparent ``NotBlankStr(...)`` narrowing, both sides of an
    ``or`` fallback, and both operands of a ``+`` concatenation, so no shape
    hides a literal: a prefix glued to a variable is as invented as the
    f-string spelling of the same value, and fails the same foreign key.

    Returns:
        ``True`` when the expression can only produce a made-up id.
    """
    if isinstance(value, ast.Constant):
        return isinstance(value.value, str)
    if isinstance(value, ast.JoinedStr):
        return True
    if isinstance(value, ast.BoolOp):
        return any(_is_fabricated(operand) for operand in value.values)
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        return _is_fabricated(value.left) or _is_fabricated(value.right)
    if isinstance(value, ast.Call):
        name = _called_name(value)
        if name in _TRANSPARENT_WRAPPERS and value.args:
            return _is_fabricated(value.args[0])
    return False


def _scan_file(path: Path, rel: str) -> list[_Hit]:
    """Return every fabricated owner id in one file.

    Returns:
        A list of :class:`_Hit` for each violating keyword.

    Raises:
        GateSourceError: If the file cannot be read or parsed (fail-closed).
    """
    text, tree = read_and_parse(path)
    marked = _marker_lines(text, rel)
    hits: list[_Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _called_name(node)
        if call not in _TARGET_CALLS:
            continue
        for kw in node.keywords:
            if kw.arg not in _OWNER_KEYWORDS or not _is_fabricated(kw.value):
                continue
            if kw.value.lineno in marked or node.lineno in marked:
                continue
            hits.append(
                _Hit(
                    rel=rel,
                    lineno=kw.value.lineno,
                    col=kw.value.col_offset,
                    keyword=str(kw.arg),
                    call=str(call),
                )
            )
    return hits


def _scan_all(project_root: Path) -> list[_Hit]:
    """Scan ``src/synthorg`` and return every fabricated owner id.

    Returns:
        A list of :class:`_Hit`.

    Raises:
        GateSourceError: If any source file cannot be read or parsed.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    hits: list[_Hit] = []
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
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        hits = _scan_all(project_root)
    except GateSourceError as exc:
        print(f"check_no_synthetic_cost_owner: {exc}", file=sys.stderr)
        return 2

    if not hits:
        return 0
    hits.sort(key=lambda h: (h.rel, h.lineno, h.col))
    for hit in hits:
        print(hit.message())
    print(
        f"\n{len(hits)} fabricated cost-owner id(s). Pass the real id, pass "
        "None when the work belongs to no agent and no task, or add "
        "'# lint-allow: synthetic-cost-owner -- <reason>' on the keyword's "
        "line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
