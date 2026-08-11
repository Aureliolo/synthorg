#!/usr/bin/env python3
"""Pre-push / CI gate: a defaulting lookup may not read a name nothing has.

``getattr(obj, "<literal>", <default>)`` whose attribute name is declared as
an attribute NOWHERE in ``src/synthorg/`` is a ghost read. The name exists on
nothing this tree defines, so the default is the only reachable outcome, the
absent-branch below it is dead, and "the attribute is missing" becomes
indistinguishable from "the attribute holds the default".

Two of those shipped, and both were found by a live run rather than by
reading the code:

* ``getattr(app_state, "tool_registry", None)``. ``AppState`` has never
  carried a tool registry. Every scrape resolved the read to ``None``, the
  fail-closed validator downstream then rejected every tool name for the life
  of the process, and the metric reported an empty allowlist as a success.
* ``getattr(state, "_connection_user", None)``. The authenticated user lives
  on the connection, not on application ``State``; ``api/auth/context.py``
  says in its own docstring that its ContextVar binding exists so a missing
  user is not masked as ``api``. The leftover helper masked it as ``api`` on
  every request, including fully authenticated ones, and its test built the
  ghost shape so the suite agreed.

Why this rule and not a broader one
-----------------------------------
A literal attribute name is statically knowable, and a three-argument
``getattr`` is precisely the construct that hides the read from ``mypy``:
written ``obj.attr``, both defects above would have been rejected where they
were written. This gate re-asks mypy's question at the level it can answer
without inference: does this name exist on anything we define? A name that
does exist on some type of ours is left alone, because deciding whether it
exists on THIS object needs type inference, which is mypy's job. Writing
``obj.attr`` is how you ask it.

Shapes deliberately not flagged
-------------------------------
* Two-argument ``getattr(obj, "x")`` raises on absence, which is the posture
  the rule wants.
* A non-literal name (``getattr(obj, field, None)``) is not statically
  knowable, so the gate says nothing rather than guessing.
* ``hasattr`` is an explicit presence test, not a substitution.
* ``dict.get`` is left alone: a dict is a partial map by construction, and
  flagging it would make this a rule about dictionaries.

Allowlist / opt-out
-------------------
Per-line ``# lint-allow: ghost-attribute-read -- <reason>``, honoured anywhere
in the call's line span. The justification after ``--`` is required, because
every legitimate ghost read is a read of a third-party object and the reason
is the only place that fact gets written down.

History lives in ``scripts/ghost_attribute_read_baseline.txt``, keyed
``path::qualname::attribute::count``. The qualified name rather than a line
number because the file is long-lived and a line key goes stale on any edit
above the site; the count because a function already approved for one ghost
read must not silently acquire a second. The baseline may only shrink
(``check_baseline_growth.py``), and a count that outlives its sites is
reported as drift rather than tolerated, since it would pre-authorise a
future ghost read.

Usage::

    uv run python scripts/check_no_ghost_attribute_read.py
    uv run python scripts/check_no_ghost_attribute_read.py --update
    uv run python scripts/check_no_ghost_attribute_read.py --files a.py b.py

Exit codes:
    0 -- every ghost read is accounted for.
    1 -- an unbaselined ghost read.
    2 -- the scan could not be trusted (bad ``--repo-root``, unreadable or
         unparseable source, an untokenisable file, a malformed baseline, or
         a baseline entry that outlived its sites).
"""

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Iterator
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
_BASELINE_REL: Final[str] = "scripts/ghost_attribute_read_baseline.txt"
_SUPPRESSION_MARKER: Final[str] = "lint-allow: ghost-attribute-read"

#: Qualname stand-in for a read at module level, so every hit has a key.
_MODULE_SCOPE: Final[str] = "<module>"

#: ``getattr(obj, "name", default)``: the only arity the rule governs.
_DEFAULTING_ARITY: Final[int] = 3

_BASELINE_ENTRY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<key>[\w./\-]+\.py::[\w.<>]+::\w+)::(?P<count>\d+)$"
)

_BASELINE_HEADER: Final[str] = (
    '# Ghost attribute reads: getattr(obj, "<literal>", <default>) where the\n'
    "# name is declared as an attribute nowhere in src/synthorg. Every entry\n"
    "# below reads a third-party object. Shrink-only: regenerate with\n"
    "#   uv run python scripts/check_no_ghost_attribute_read.py --update\n"
    "# after REMOVING a read. A new read needs a per-line\n"
    "# '# lint-allow: ghost-attribute-read -- <reason>' instead.\n"
)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One defaulting read of a name this tree declares nowhere."""

    rel: str
    lineno: int
    col: int
    attribute: str
    qualname: str

    @property
    def group_key(self) -> str:
        """Return the baseline identity of this hit's site."""
        return f"{self.rel}::{self.qualname}::{self.attribute}"

    def message(self) -> str:
        """Return the human-facing violation message."""
        return (
            f"{self.rel}:{self.lineno}:{self.col}: getattr(..., "
            f"{self.attribute!r}, <default>) reads a name declared as an "
            f"attribute nowhere in {_SCAN_ROOT_REL}, so the default is the "
            f"only outcome and the absent-branch is dead. Read the thing that "
            f"holds the answer, or mark a third-party read with "
            f"'# {_SUPPRESSION_MARKER} -- <reason>'."
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
            f"check_no_ghost_attribute_read: git ls-files failed in "
            f"{project_root} ({type(exc).__name__}: {exc}); falling back to "
            f"rglob (scope widens to include untracked / gitignored files).",
            file=sys.stderr,
        )
        return sorted(
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        )
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p and p.endswith(".py")]
    if not paths:
        # git answered about a DIFFERENT tree: a sandbox root nested inside an
        # unrelated checkout resolves the command successfully and lists
        # nothing. Discovering nothing here would silently pass an empty scan
        # AND collapse the declaration set, so fall back rather than trust it.
        return sorted(
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        )
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _is_valid_marker(comment_token: str) -> bool:
    """Return True iff *comment_token* is a justified suppression marker.

    Returns:
        ``True`` for ``# lint-allow: ghost-attribute-read -- <reason>``.
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


# ── declaration pass ────────────────────────────────────────────


def _string_elements(value: ast.expr | None) -> Iterator[str]:
    """Yield the string constants of a tuple or list literal."""
    if isinstance(value, ast.Tuple | ast.List):
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                yield element.value


def _class_body_names(node: ast.ClassDef) -> Iterator[str]:
    """Yield every attribute a class body declares directly.

    Covers the annotated form Pydantic, dataclasses and ``@ontology_entity``
    all reduce to, the bare assignment, methods and properties (a bound method
    is an attribute), and the ``__slots__`` literal.

    Args:
        node: The class definition to read.

    Yields:
        Each declared attribute name.
    """
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            yield stmt.target.id
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    yield target.id
                    if target.id == "__slots__":
                        yield from _string_elements(stmt.value)
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            yield stmt.name


def _module_declarations(tree: ast.Module) -> Iterator[str]:
    """Yield every attribute name one parsed module declares.

    Args:
        tree: The parsed module.

    Yields:
        Each declared attribute name, with duplicates.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield from _class_body_names(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    yield target.attr
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
            yield node.target.attr
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) == _DEFAULTING_ARITY
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            yield node.args[1].value


def declared_attribute_names(files: Iterable[tuple[Path, str]]) -> set[str]:
    """Return every name the scanned tree declares as an attribute.

    Args:
        files: ``(absolute_path, relative_path)`` pairs to read.

    Returns:
        The declared attribute names.

    Raises:
        GateSourceError: If a file cannot be read or parsed (fail-closed: a
            skipped module would shrink the declaration set and turn honest
            reads into reported ghosts).
    """
    names: set[str] = set()
    for path, _rel in files:
        _text, tree = read_and_parse(path)
        names.update(_module_declarations(tree))
    return names


# ── read pass ───────────────────────────────────────────────────


def _scoped_nodes(node: ast.AST, qualname: str) -> Iterator[tuple[ast.AST, str]]:
    """Yield every descendant of *node* paired with its enclosing qualname.

    The qualname changes only at a ``def`` / ``async def`` / ``class``
    boundary, so a read buried in a comprehension or a ``with`` block is
    still attributed to the function that contains it, which is what makes
    the baseline key survive an unrelated edit above the site.

    Args:
        node: The node whose descendants to walk.
        qualname: The qualified name of *node*'s own scope.

    Yields:
        ``(descendant, enclosing_qualname)`` pairs.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            inner = (
                child.name if qualname == _MODULE_SCOPE else f"{qualname}.{child.name}"
            )
            yield child, qualname
            yield from _scoped_nodes(child, inner)
        else:
            yield child, qualname
            yield from _scoped_nodes(child, qualname)


def _ghost_attribute(node: ast.Call, declared: frozenset[str]) -> str | None:
    """Return the ghost attribute name *node* reads, or ``None``.

    Args:
        node: The candidate call.
        declared: Every attribute name the tree declares.

    Returns:
        The literal attribute name when *node* is a defaulting ``getattr``
        of a name nothing declares, else ``None``.
    """
    if not isinstance(node.func, ast.Name):
        return None
    # A keyword argument means this is somebody else's getattr: the builtin
    # takes none, so a call carrying one cannot be the shape being governed.
    if node.func.id != "getattr" or node.keywords:
        return None
    if len(node.args) != _DEFAULTING_ARITY:
        return None
    name = node.args[1]
    if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
        return None
    return None if name.value in declared else name.value


def scan_file(path: Path, rel: str, declared: frozenset[str]) -> list[_Hit]:
    """Return every unsuppressed ghost read in one file.

    Args:
        path: The file to scan.
        rel: Its repository-relative posix path, used in the hit key.
        declared: Every attribute name the tree declares.

    Returns:
        The hits, in source order.

    Raises:
        GateSourceError: If the file cannot be read, parsed, or tokenised.
    """
    text, tree = read_and_parse(path)
    marked = _marker_lines(text, rel)
    hits: list[_Hit] = []
    for node, qualname in _scoped_nodes(tree, _MODULE_SCOPE):
        if not isinstance(node, ast.Call):
            continue
        attribute = _ghost_attribute(node, declared)
        if attribute is None:
            continue
        span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
        if any(line in marked for line in span):
            continue
        hits.append(
            _Hit(
                rel=rel,
                lineno=node.lineno,
                col=node.col_offset,
                attribute=attribute,
                qualname=qualname,
            )
        )
    return sorted(hits, key=lambda h: (h.lineno, h.col))


# ── baseline ────────────────────────────────────────────────────


def _baseline_path(project_root: Path) -> Path:
    """Return the baseline file location anchored at *project_root*."""
    return project_root / _BASELINE_REL


def _load_baseline(path: Path) -> dict[str, int]:
    """Return the approved ``group_key -> count`` map.

    Returns:
        The approved counts (empty when the file is absent).

    Raises:
        ValueError: On a malformed or duplicate entry, or an unreadable file,
            so a corrupt baseline fails loud rather than passing a silently
            truncated allowlist.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{_BASELINE_REL}: cannot read baseline ({type(exc).__name__}: {exc})"
        raise ValueError(msg) from exc
    approved: dict[str, int] = {}
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _BASELINE_ENTRY_RE.match(stripped)
        if match is None:
            errors.append(
                f"{_BASELINE_REL}:{lineno}: malformed entry (expected "
                f"'path::qualname::attribute::count', got {stripped!r})"
            )
            continue
        key = match.group("key")
        if key in approved:
            errors.append(f"{_BASELINE_REL}:{lineno}: duplicate entry for {key!r}")
            continue
        approved[key] = int(match.group("count"))
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        msg = (
            f"{_BASELINE_REL}: baseline failed validation ({len(errors)} "
            f"error{'s' if len(errors) != 1 else ''}); regenerate with "
            f"'uv run python scripts/check_no_ghost_attribute_read.py "
            f"--update' or fix by hand."
        )
        raise ValueError(msg)
    return approved


def _write_baseline(counts: dict[str, int], path: Path) -> None:
    """Sort + write the live *counts* as a baseline file."""
    body = "".join(f"{key}::{counts[key]}\n" for key in sorted(counts))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_BASELINE_HEADER + body, encoding="utf-8")


def _live_counts(hits: list[_Hit]) -> dict[str, int]:
    """Return the per-site hit counts keyed by baseline identity."""
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.group_key] = counts.get(hit.group_key, 0) + 1
    return counts


# ── scan ────────────────────────────────────────────────────────


def _scan(
    project_root: Path, targets: list[tuple[Path, str]] | None = None
) -> list[_Hit]:
    """Scan for ghost reads, deriving the declaration set tree-wide.

    The declaration pass always reads the whole scanned tree, even in
    ``--files`` mode: whether a name exists is a tree-wide fact, and deriving
    it from one file would report every honest read in that file as a ghost.

    Args:
        project_root: Repository root to scan.
        targets: The files to run the read pass over; the whole tree by
            default.

    Returns:
        Every unsuppressed ghost read, in file then line order.

    Raises:
        GateSourceError: If any source file cannot be read, parsed, or
            tokenised.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    tree_files = _git_tracked_python_files(abs_root, project_root)
    declared = frozenset(declared_attribute_names(tree_files))
    hits: list[_Hit] = []
    for path, rel in tree_files if targets is None else targets:
        hits.extend(scan_file(path, rel, declared))
    return sorted(hits, key=lambda h: (h.rel, h.lineno, h.col))


def _resolve_targets(project_root: Path, files: list[Path]) -> list[tuple[Path, str]]:
    """Return ``(abs, rel)`` pairs for the ``--files`` selection.

    Files outside the scanned tree are dropped: the rule is scoped to
    ``src/synthorg``, and an edit-time caller passes whatever it just touched.

    Returns:
        The in-scope selection.

    Raises:
        ProjectRootError: If a named file is not inside *project_root*.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    targets: list[tuple[Path, str]] = []
    for raw in files:
        try:
            resolved = raw.resolve(strict=True)
        except OSError as exc:
            msg = f"--files names an unreadable path: {raw} ({exc})"
            raise ProjectRootError(msg) from exc
        if not resolved.is_relative_to(abs_root) or resolved.suffix != ".py":
            continue
        targets.append((resolved, resolved.relative_to(project_root).as_posix()))
    return targets


def _report_drift(drift: list[tuple[str, int, int]]) -> None:
    """Print the diagnosis for baseline entries that outlived their sites."""
    for key, approved, live in drift:
        print(
            f"{_BASELINE_REL}: {key} is approved for {approved} ghost read(s) "
            f"but {live} remain(s). An entry that outlives its sites "
            f"pre-authorises a future one.",
            file=sys.stderr,
        )
    print(
        f"\n{len(drift)} stale baseline entr"
        f"{'y' if len(drift) == 1 else 'ies'}. Fix any violations reported "
        f"above first, then regenerate with 'uv run python "
        f"scripts/check_no_ghost_attribute_read.py --update'.",
        file=sys.stderr,
    )


# ── commands ────────────────────────────────────────────────────


def cmd_update(project_root: Path) -> int:
    """Regenerate the baseline from the current tree.

    The scan runs to completion BEFORE anything is written: a scan that could
    not be trusted must never overwrite a good baseline with a short one,
    which would look like a legitimate shrink to every downstream guard.

    Returns:
        ``0`` on success, ``2`` if the scan or the write failed.
    """
    try:
        hits = _scan(project_root)
    except GateSourceError as exc:
        print(
            f"check_no_ghost_attribute_read: {exc}\nBaseline left untouched.",
            file=sys.stderr,
        )
        return 2
    counts = _live_counts(hits)
    path = _baseline_path(project_root)
    try:
        _write_baseline(counts, path)
    except OSError as exc:
        print(
            f"check_no_ghost_attribute_read: could not write baseline {path} "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return 2
    print(f"Wrote {len(counts)} entries ({len(hits)} reads) to {_BASELINE_REL}.")
    return 0


def cmd_scan(project_root: Path, files: list[Path]) -> int:
    """Check every ghost read against the baseline.

    Returns:
        ``0`` when clean, ``1`` on an unaccounted read, ``2`` when the scan
        itself could not be trusted.
    """
    try:
        approved = _load_baseline(_baseline_path(project_root))
        targets = _resolve_targets(project_root, files) if files else None
    except (ValueError, ProjectRootError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        hits = _scan(project_root, targets)
    except GateSourceError as exc:
        print(f"check_no_ghost_attribute_read: {exc}", file=sys.stderr)
        return 2

    counts = _live_counts(hits)
    # A site over its approved count reports EVERY read at it: nothing
    # distinguishes the approved read from the new one, so naming them all is
    # the only honest answer, and the summary says how many were approved.
    over = {key for key, live in counts.items() if live > approved.get(key, 0)}
    violations = [h for h in hits if h.group_key in over]
    for hit in violations:
        print(hit.message())
    if violations:
        allowed = sum(approved.get(key, 0) for key in over)
        print(
            f"\n{len(violations)} ghost attribute read(s) across {len(over)} "
            f"site(s) approved for {allowed}. The name is declared on nothing "
            f"this tree defines, so the default is the only outcome. Read the "
            f"thing that holds the answer, or mark a third-party read with "
            f"'# {_SUPPRESSION_MARKER} -- <reason>'.",
            file=sys.stderr,
        )
        return 1

    # Drift is only meaningful over a whole-tree scan: a --files run sees a
    # subset, so every unscanned entry would read as stale.
    if targets is None:
        drift = [
            (key, approved[key], counts.get(key, 0))
            for key in sorted(approved)
            if approved[key] > counts.get(key, 0)
        ]
        if drift:
            _report_drift(drift)
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The gate exit code (0 clean, 1 violation, 2 untrustworthy scan).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to this script's repo).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Regenerate the baseline file from the current tree.",
    )
    parser.add_argument(
        "--files",
        type=Path,
        nargs="*",
        default=[],
        help="Scan only these files (edit-time mode); the declaration pass "
        "still reads the whole tree.",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.update:
        return cmd_update(project_root)
    return cmd_scan(project_root, list(args.files))


if __name__ == "__main__":
    raise SystemExit(main())
