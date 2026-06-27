#!/usr/bin/env python3
"""Pre-push / CI gate: every ``cost_recording_scope()`` call passes ``purpose=``.

``cost_recording_scope`` is the per-call cost-recording chokepoint
(``synthorg.providers.cost_recording``). Its ``purpose`` keyword stamps the
emitted ``CostRecord.prompt_class_id`` so spend and latency can be sliced by
prompt purpose. A call that omits ``purpose=`` records cost with no purpose
attribution and that blind spot is silent: nothing else surfaces it. This gate
makes the omission loud -- every call site must pass ``purpose=`` explicitly (a
``PromptPurposeId`` when the call has a registered system prompt purpose, or
``None`` when it deliberately has none), so attribution cannot regress by a new
call site quietly skipping the dimension.

Detection
---------
AST-walk every tracked ``*.py`` under ``src/synthorg/`` and flag each call to
``cost_recording_scope`` whose keyword arguments do not include ``purpose``.

Allowlist / opt-out
-------------------
Per-line opt-out: append ``# lint-allow: cost-scope-purpose -- <reason>`` to the
call's opening line. The justification after ``--`` is required and must be
non-empty.

Baseline
--------
``scripts/cost_scope_purpose_baseline.txt`` lists the ``cost_recording_scope``
call sites that do not yet pass ``purpose=``. Each line is ``path:lineno:col``.
The list shrinks monotonically: a site drops out once it gains ``purpose=``.
Regenerate (rare; requires explicit user approval) with ``--update``.

Usage::

    uv run python scripts/check_cost_scope_purpose.py
    uv run python scripts/check_cost_scope_purpose.py --update

Exit codes:
    0 -- no violations outside the baseline.
    1 -- a new untagged ``cost_recording_scope`` call was detected.
    2 -- configuration error (bad ``--repo-root``, an unreadable or malformed
         baseline, or a source file that could not be read, parsed, or
         tokenised -- fail-closed).
"""

import argparse
import ast
import io
import re
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
_TARGET_CALL: Final[str] = "cost_recording_scope"
_TARGET_MODULE: Final[str] = "synthorg.providers.cost_recording"
_REQUIRED_KEYWORD: Final[str] = "purpose"
_SUPPRESSION_MARKER: Final[str] = "lint-allow: cost-scope-purpose"
_BASELINE_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"^.+:\d+:\d+$")

_BASELINE_HEADER: Final[str] = """\
# cost_recording_scope() call sites that do not yet pass purpose=. Each line
# is `path:lineno:col` (POSIX path, 1-indexed line, 0-indexed column) sorted
# in deterministic order.
#
# scripts/check_cost_scope_purpose.py reads this file to suppress violations
# at these exact locations. A call site NOT in this list fails the pre-push
# hook. The list shrinks monotonically: a site drops out once it passes
# purpose=.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_cost_scope_purpose.py --update
"""


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One ``cost_recording_scope`` call missing ``purpose=``."""

    rel: str
    lineno: int
    col: int

    def __post_init__(self) -> None:
        """Reject coordinates the AST can never legally produce.

        Surfaces a scan-loop bug immediately rather than letting an invalid
        ``path:lineno:col`` reach the baseline, where it would only fail much
        later on round-trip.

        Raises:
            ValueError: If ``rel`` is empty, ``lineno`` < 1, or ``col`` < 0.
        """
        if not self.rel:
            msg = "rel must not be empty"
            raise ValueError(msg)
        if self.lineno < 1:
            msg = f"lineno must be >= 1, got {self.lineno}"
            raise ValueError(msg)
        if self.col < 0:
            msg = f"col must be >= 0, got {self.col}"
            raise ValueError(msg)

    def baseline_key(self) -> str:
        """Return the ``path:lineno:col`` baseline identity for this hit."""
        return f"{self.rel}:{self.lineno}:{self.col}"

    def message(self) -> str:
        """Return the human-facing violation message."""
        return (
            f"{self.rel}:{self.lineno}:{self.col}: cost_recording_scope() call "
            f"missing required '{_REQUIRED_KEYWORD}=' keyword."
        )


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Returns:
        The resolved project-root directory.

    Raises:
        ProjectRootError: If *repo_root* cannot be resolved to an existing
            path, or resolves to something that is not a directory.
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


def _baseline_path(project_root: Path) -> Path:
    """Return the baseline file location anchored at *project_root*."""
    return project_root / "scripts" / "cost_scope_purpose_baseline.txt"


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` under *abs_root* as ``(abs, rel)``.

    Falls back to :meth:`Path.rglob` when ``git`` is unavailable or fails;
    the fallback widens scope to include untracked / gitignored files, so a
    stderr warning is emitted to make the semantic change visible rather than
    silently mutating what the gate scans.

    Returns:
        A list of ``(absolute_path, posix_relative_path)`` pairs.
    """
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        # Pass the directory itself (recursive) and filter ``.py`` in Python.
        # A ``dir/*.py`` pathspec is brittle: its recursion depends on git
        # glob settings, so a directory pathspec is the robust choice.
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", rel_root],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        # ``OSError`` (not just ``FileNotFoundError``) so a non-executable or
        # otherwise unspawnable git binary still degrades to the rglob scan
        # instead of crashing with a misleading exit 1.
        print(
            f"check_cost_scope_purpose: git ls-files failed in "
            f"{project_root} ({type(exc).__name__}: {exc}); falling back "
            f"to rglob (scope widens to include untracked / gitignored "
            f"files).",
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

    The marker must be followed by ``--`` and non-empty justification text,
    mirroring ``check_no_magic_numbers.py``.

    Returns:
        ``True`` for ``# lint-allow: cost-scope-purpose -- <reason>``.
    """
    comment = comment_token.lstrip("#").strip()
    if not comment.startswith(_SUPPRESSION_MARKER):
        return False
    suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
    return suffix.startswith("--") and bool(suffix[2:].strip())


def _marker_lines(text: str, rel: str) -> set[int]:
    """Return the 1-indexed line numbers carrying a valid suppression marker.

    Tokenises the WHOLE source (not a single line) so a marker on the
    unbalanced opening line of a multi-line ``cost_recording_scope(`` call is
    still recognised; a single-line tokenise would raise on the open paren.

    Returns:
        The set of line numbers whose comment is a justified marker.

    Raises:
        GateSourceError: If the (already ast-parsed) source fails to tokenise,
            so a dropped marker fails the gate loud rather than silently.
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


def _target_call_names(tree: ast.AST) -> set[str]:
    """Return every local name that resolves to ``cost_recording_scope``.

    Covers each binding form so a call cannot dodge the ``purpose=`` check by
    renaming the chokepoint:

    * the canonical name itself;
    * an import alias
      (``from synthorg.providers.cost_recording import cost_recording_scope as
      crs``);
    * a plain post-import rebinding (``crs = cost_recording_scope``), resolved
      to a fixpoint so a multi-hop chain (``a = cost_recording_scope; b = a``)
      is collapsed too. ``ast.walk`` yields no source order, so a single pass
      could otherwise miss ``b = a`` visited before its ``a`` binding.

    Attribute access (``module.cost_recording_scope(...)``) is matched
    separately in :func:`_is_target_call` on the attribute name alone.

    Returns:
        The set of local names that refer to ``cost_recording_scope``.
    """
    names = {_TARGET_CALL}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _TARGET_MODULE:
            for alias in node.names:
                if alias.name == _TARGET_CALL:
                    names.add(alias.asname or alias.name)
    assigns = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    changed = True
    while changed:
        changed = False
        for node in assigns:
            value = node.value
            if not isinstance(value, ast.Name) or value.id not in names:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True
    return names


def _is_target_call(node: ast.Call, target_names: set[str]) -> bool:
    """Return True iff *node* calls ``cost_recording_scope`` (name or attr).

    *target_names* carries the canonical name plus any alias or rebinding that
    resolves to the chokepoint (see :func:`_target_call_names`). Attribute
    access (``module.cost_recording_scope(...)``) still matches on the
    attribute name alone, since the base object is opaque to a static scan.

    Returns:
        ``True`` when *node* calls the chokepoint under any reachable name.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in target_names
    if isinstance(func, ast.Attribute):
        return func.attr == _TARGET_CALL
    return False


def _has_purpose_keyword(node: ast.Call) -> bool:
    """Return True iff *node* passes an explicit ``purpose=`` keyword.

    A ``**kwargs`` spread (``kw.arg is None``) is NOT counted: the gate
    cannot prove ``purpose`` is present, so it fails closed and the site
    must opt out explicitly.

    Returns:
        ``True`` when a ``purpose=`` keyword is present.
    """
    return any(kw.arg == _REQUIRED_KEYWORD for kw in node.keywords)


def _scan_file(path: Path, rel: str) -> list[_Hit]:
    """Return the untagged ``cost_recording_scope`` calls in one file.

    Returns:
        A list of :class:`_Hit` for each violating call site.

    Raises:
        GateSourceError: If the file cannot be read or parsed (fail-closed).
    """
    text, tree = read_and_parse(path)
    marked = _marker_lines(text, rel)
    target_names = _target_call_names(tree)
    hits: list[_Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_target_call(node, target_names):
            continue
        if _has_purpose_keyword(node) or node.lineno in marked:
            continue
        hits.append(_Hit(rel=rel, lineno=node.lineno, col=node.col_offset))
    return hits


def _scan_all(project_root: Path) -> list[_Hit]:
    """Scan ``src/synthorg`` and return every untagged call site.

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


def _baseline_sort_key(entry: str) -> tuple[str, int, int]:
    """Return ``(path, lineno, col)`` so numeric components order numerically."""
    path, lineno, col = entry.rsplit(":", 2)
    return (path, int(lineno), int(col))


def _load_baseline(path: Path) -> set[str]:
    """Return the set of allowlisted ``path:lineno:col`` baseline entries.

    Returns:
        The set of frozen baseline entries (empty when the file is absent).

    Raises:
        ValueError: On malformed/duplicate entries or an unreadable file, so
            a corrupt baseline fails the gate loud rather than passing a
            silently-truncated allowlist.
    """
    if not path.exists():
        return set()
    rel_path = (
        path.relative_to(_REPO_ROOT).as_posix()
        if path.is_relative_to(_REPO_ROOT)
        else str(path)
    )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{rel_path}: cannot read baseline ({type(exc).__name__}: {exc})"
        raise ValueError(msg) from exc
    entries: set[str] = set()
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _BASELINE_ENTRY_RE.match(stripped):
            errors.append(
                f"{rel_path}:{lineno}: malformed entry "
                f"(expected 'path:lineno:col', got {stripped!r})"
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
            f"regenerate with 'uv run python scripts/check_cost_scope_purpose.py "
            f"--update' or fix by hand."
        )
        raise ValueError(msg)
    return entries


def _write_baseline(hits: list[_Hit], path: Path) -> None:
    """Sort + write *hits* as a baseline file."""
    keys = sorted({hit.baseline_key() for hit in hits}, key=_baseline_sort_key)
    body = _BASELINE_HEADER + "\n".join(keys) + "\n"
    path.write_text(body, encoding="utf-8")


def cmd_update(project_root: Path) -> int:
    """Regenerate the baseline from the current tree.

    Returns:
        ``0`` on success, ``2`` if a source file could not be read or parsed,
        or the baseline could not be written.
    """
    try:
        hits = _scan_all(project_root)
    except GateSourceError as exc:
        print(f"check_cost_scope_purpose: {exc}", file=sys.stderr)
        return 2
    baseline_path = _baseline_path(project_root)
    try:
        _write_baseline(hits, baseline_path)
    except OSError as exc:
        print(
            f"check_cost_scope_purpose: could not write baseline "
            f"{baseline_path} ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return 2
    rel = baseline_path.relative_to(project_root).as_posix()
    print(
        f"Wrote {len({h.baseline_key() for h in hits})} entries to {rel}.",
        file=sys.stderr,
    )
    return 0


def cmd_scan(project_root: Path) -> int:
    """Scan and exit non-zero on any new violation outside the baseline.

    Returns:
        ``0`` when clean, ``1`` on a new violation, ``2`` on a read/parse or
        baseline error.
    """
    try:
        baseline = _load_baseline(_baseline_path(project_root))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        hits = _scan_all(project_root)
    except GateSourceError as exc:
        print(f"check_cost_scope_purpose: {exc}", file=sys.stderr)
        return 2
    live_keys = {h.baseline_key() for h in hits}
    stale_entries = sorted(baseline - live_keys, key=_baseline_sort_key)
    if stale_entries:
        baseline_rel = _baseline_path(project_root).relative_to(project_root).as_posix()
        for entry in stale_entries:
            print(f"{baseline_rel}: stale baseline entry {entry}", file=sys.stderr)
        print(
            f"\n{len(stale_entries)} baseline entr"
            f"{'y' if len(stale_entries) == 1 else 'ies'} no longer match a "
            "violating call site. A fixed site that stays allowlisted would "
            "silently suppress a future omission reusing the same "
            "path:lineno:col. Remove the stale line(s), or regenerate with "
            "'uv run python scripts/check_cost_scope_purpose.py --update'.",
            file=sys.stderr,
        )
        return 2
    new_violations = [h for h in hits if h.baseline_key() not in baseline]
    if not new_violations:
        return 0
    new_violations.sort(key=lambda h: (h.rel, h.lineno, h.col))
    for hit in new_violations:
        print(hit.message())
    print(
        f"\n{len(new_violations)} cost_recording_scope() call(s) missing "
        f"'{_REQUIRED_KEYWORD}='. Pass a PromptPurposeId (or explicit None when "
        "the call has no registered system prompt purpose), or add "
        "'# lint-allow: cost-scope-purpose -- <reason>' on the call's opening "
        "line.",
        file=sys.stderr,
    )
    return 1


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
        "--update",
        action="store_true",
        help="Regenerate the baseline file from the current tree.",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.update:
        return cmd_update(project_root)
    return cmd_scan(project_root)


if __name__ == "__main__":
    raise SystemExit(main())
