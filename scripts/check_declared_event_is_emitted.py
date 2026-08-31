#!/usr/bin/env python3
"""Pre-push / CI gate: every declared event constant is emitted somewhere.

A call-graph trace measured 247 of 4,357 event constants (5.7%) never
emitted anywhere outside the module that declares them: a deleted feature
leaving its taxonomy behind, a stale duplicate of a live name, or a designed
taxonomy only partly implemented. An unemitted event constant is the cheapest
reliable marker of an unwired feature, so this is the rule that stops that
ratio drifting back up.

Detection
---------
Every module-level ``NAME = <string literal>`` (annotated ``Final[str]``,
``Final[LiteralString]``, a bare ``str`` annotation, or unannotated -- the
annotation is not what makes it an event constant) declared anywhere under
``src/synthorg/observability/events/`` is population. Population is derived
by AST, never listed, so a renamed or added module is picked up automatically.
``__all__`` is excluded because its value is a list, not a string.

A constant is emitted when its bare identifier appears as an ``ast.Name`` in
Load context somewhere in ``src/synthorg/`` (the events package itself
EXCLUDED, so a re-export through ``events/__init__.py`` is not an emission),
``evals/``, or ``scripts/``. AST, not grep or a docstring/comment scan: every
production reference in this codebase imports the bare name and uses it
directly (``from ... import NAME`` then ``NAME``), never ``events.NAME``
attribute access, so a Name-context walk is sufficient and a comment or
docstring mentioning the name is correctly excluded.

``tests/`` is deliberately NOT a consumer: a constant only the test suite
names is dead, which is the state 37 constants shipped in. A test
importing a constant to assert its value is not evidence anything emits it.

What it does NOT do
--------------------
It does not check that an emission site is reachable at runtime (that is
``check_runtime_reachability.py``'s narrower, manifest-pinned job, and its own
docstring rejects being a transitive call-graph engine). A constant referenced
from dead code still passes this gate; call-graph reachability is a different,
harder question this gate does not attempt.

Allowlist / opt-out
--------------------
Per-line opt-out: append ``# lint-allow: unemitted-event -- <reason>`` to any
line of the declaring statement. The justification after ``--`` is required.
The only legitimate case is a constant an external consumer reads by value
(a dashboard, a Grafana query, a doc-verified alert name) rather than by
importing the Python identifier.

Baseline
--------
Re-verification found the surviving population dominated by a shape a
per-line opt-out cannot honestly cover: most of the 260 that survived the
deletion pass are error paths that exist and simply log nothing at the
failure site (``PERSISTENCE_SETTING_SAVE_FAILED``, ``DB_CONNECTION_FAILED``,
...), not dead taxonomy. Deleting them would remove real observability;
marking them ``lint-allow`` would misuse a marker whose only legitimate
reason is an external value-consumer. ``scripts/declared_event_baseline.txt``
(shrink-only, same shape as ``ghost_attribute_read_baseline.txt``) is the
honest record instead: a name in it is a KNOWN gap, not an approved one, and
the file can only shrink as each is wired or confirmed dead in a follow-up
pass. A name newly missing its emitter fails the gate as a new violation
regardless of the baseline; a baseline entry that has since been wired or
deleted is stale and blocks the push until the baseline is regenerated with
``--update`` (never grown to admit a name that never violated).

Usage::

    uv run python scripts/check_declared_event_is_emitted.py
    uv run python scripts/check_declared_event_is_emitted.py --update

Exit codes:
    0 -- every declared constant is emitted or baselined.
    1 -- at least one declared constant is unemitted and not in the baseline.
    2 -- configuration error (bad ``--repo-root``, an empty population, a
         source file that could not be read, parsed, or tokenised, a
         malformed baseline, or a baseline entry that outlived its
         violation -- fail-closed).
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
_EVENTS_ROOT_REL: Final[str] = "src/synthorg/observability/events"
_SRC_ROOT_REL: Final[str] = "src/synthorg"
_EXTRA_CONSUMER_ROOTS_REL: Final[tuple[str, ...]] = ("evals", "scripts")
_SUPPRESSION_MARKER: Final[str] = "lint-allow: unemitted-event"
_BASELINE_REL: Final[str] = "scripts/declared_event_baseline.txt"
_BASELINE_HEADER: Final[str] = (
    "# Known-unemitted event constants: a declared name with no reference\n"
    "# outside observability/events/. A name here is a KNOWN GAP, not an\n"
    "# approved one -- most are a real error path that logs nothing at the\n"
    "# failure site, confirmed not to be dead taxonomy. Shrink-only:\n"
    "# regenerate with\n"
    "#   uv run python scripts/check_declared_event_is_emitted.py --update\n"
    "# after WIRING or DELETING a name. A newly-unemitted name not already\n"
    "# here fails the gate as a fresh violation.\n"
)


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Hit:
    """One declared event constant with no emission site."""

    name: str
    rel: str
    lineno: int

    def message(self) -> str:
        """Return the human-facing violation message."""
        return (
            f"{self.rel}:{self.lineno}: {self.name} is declared and never "
            f"referenced outside observability/events/. Emit it, delete it, "
            f"or justify it with "
            f"'# lint-allow: unemitted-event -- <reason>'."
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
            f"check_declared_event_is_emitted: git ls-files failed in "
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
        ``True`` for ``# lint-allow: unemitted-event -- <reason>``.
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


def _is_string_constant(value: ast.expr | None) -> bool:
    """Whether *value* is a plain string literal.

    Returns:
        ``True`` for a string ``ast.Constant``; ``False`` for anything else
        (a list, a call, ``None``), which excludes ``__all__`` and any
        computed value from the declared population.
    """
    return isinstance(value, ast.Constant) and isinstance(value.value, str)


def _declared_constants(
    events_root: Path,
    project_root: Path,
) -> dict[str, tuple[str, int, int]]:
    """Return every declared event constant as ``name -> (rel, lineno, end)``.

    Returns:
        A mapping from constant name to its declaring file, start line, and
        end line (the whole statement's span, for suppression-marker lookup).

    Raises:
        GateSourceError: If a source file cannot be read or parsed, or if the
            same name is declared twice: a silent last-write-wins collision
            in this dict would drop one declaration's suppression marker and
            location from every report without either declarer noticing.
    """
    declared: dict[str, tuple[str, int, int]] = {}
    for path, rel in _git_tracked_python_files(events_root, project_root):
        if path.name == "__init__.py":
            continue
        _, tree = read_and_parse(path)
        for node in tree.body:
            target: ast.Name | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target = node.target
                value = node.value
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                target = node.targets[0]
                value = node.value
            if target is None or not _is_string_constant(value):
                continue
            end_lineno = node.end_lineno if node.end_lineno is not None else node.lineno
            if target.id in declared:
                prev_rel, prev_lineno, _ = declared[target.id]
                msg = (
                    f"{rel}:{node.lineno}: {target.id} is already declared at "
                    f"{prev_rel}:{prev_lineno}; a duplicate name would "
                    "silently collide into whichever declaration is scanned "
                    "last."
                )
                raise GateSourceError(msg)
            declared[target.id] = (rel, node.lineno, end_lineno)
    return declared


def _referenced_names(roots: list[Path], project_root: Path) -> set[str]:
    """Return every identifier referenced (Load context) under *roots*.

    Returns:
        The set of bare names appearing in ``ast.Name`` Load-context nodes.

    Raises:
        GateSourceError: If a source file cannot be read or parsed.
    """
    names: set[str] = set()
    for root in roots:
        for path, _ in _git_tracked_python_files(root, project_root):
            _, tree = read_and_parse(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    names.add(node.id)
    return names


def _scan(project_root: Path) -> list[_Hit]:
    """Return every declared event constant with no emission site.

    Returns:
        A list of :class:`_Hit`, sorted by file then line.

    Raises:
        GateSourceError: If any source file cannot be read, parsed, or
            tokenised.
    """
    events_root = project_root / _EVENTS_ROOT_REL
    declared = _declared_constants(events_root, project_root)

    consumer_roots = [
        project_root / _SRC_ROOT_REL,
        *(project_root / rel for rel in _EXTRA_CONSUMER_ROOTS_REL),
    ]
    # The events package is excluded from the reference scan (not merely
    # each constant's own module) so a re-export through events/__init__.py
    # can never count as an emission.
    referenced = _referenced_names(consumer_roots, project_root) - _referenced_names(
        [events_root], project_root
    )

    marker_cache: dict[str, set[int]] = {}
    hits: list[_Hit] = []
    for name, (rel, lineno, end_lineno) in declared.items():
        if name in referenced:
            continue
        if rel not in marker_cache:
            text, _ = read_and_parse(project_root / rel)
            marker_cache[rel] = _marker_lines(text, rel)
        marked = marker_cache[rel]
        if any(line in marked for line in range(lineno, end_lineno + 1)):
            continue
        hits.append(_Hit(name=name, rel=rel, lineno=lineno))
    hits.sort(key=lambda h: (h.rel, h.lineno, h.name))
    return hits


def _baseline_path(project_root: Path) -> Path:
    """Return the baseline file location anchored at *project_root*."""
    return project_root / _BASELINE_REL


def _load_baseline(path: Path) -> set[str]:
    """Return the baselined constant names.

    Returns:
        The baselined names (empty when the file is absent).

    Raises:
        ValueError: On a duplicate entry or an unreadable file, so a corrupt
            baseline fails loud rather than passing a silently truncated
            allowlist.
    """
    if not path.exists():
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{_BASELINE_REL}: cannot read baseline ({type(exc).__name__}: {exc})"
        raise ValueError(msg) from exc
    names: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in names:
            msg = f"{_BASELINE_REL}:{lineno}: duplicate entry for {stripped!r}"
            raise ValueError(msg)
        names.add(stripped)
    return names


def _write_baseline(names: set[str], path: Path) -> None:
    """Sort + write the live *names* as a baseline file."""
    body = "".join(f"{name}\n" for name in sorted(names))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_BASELINE_HEADER + body, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The gate exit code (0 clean, 1 new violation, 2 configuration error
        or stale baseline).
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
        help="Regenerate the baseline from the current tree.",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    events_root = project_root / _EVENTS_ROOT_REL
    if not events_root.is_dir():
        print(
            f"check_declared_event_is_emitted: events root not found: {events_root}",
            file=sys.stderr,
        )
        return 2

    try:
        hits = _scan(project_root)
    except GateSourceError as exc:
        print(f"check_declared_event_is_emitted: {exc}", file=sys.stderr)
        return 2

    if not _declared_constants(events_root, project_root):
        print(
            "check_declared_event_is_emitted: zero declared event constants "
            "found; the events package moved or the scan is misconfigured.",
            file=sys.stderr,
        )
        return 2

    live_names = {hit.name for hit in hits}

    if args.update:
        _write_baseline(live_names, _baseline_path(project_root))
        print(f"Wrote {len(live_names)} entries to {_BASELINE_REL}.")
        return 0

    try:
        baseline = _load_baseline(_baseline_path(project_root))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    new_violations = [hit for hit in hits if hit.name not in baseline]
    if new_violations:
        for hit in new_violations:
            print(hit.message())
        print(
            f"\n{len(new_violations)} new unemitted event constant(s), not in "
            f"{_BASELINE_REL}. Emit it, delete it, add '# lint-allow: "
            "unemitted-event -- <reason>' on the declaration, or confirm it "
            "is a known real gap and add it with --update.",
            file=sys.stderr,
        )
        return 1

    stale = sorted(baseline - live_names)
    if stale:
        print(
            f"{_BASELINE_REL}: {len(stale)} stale entr"
            f"{'y' if len(stale) == 1 else 'ies'} (no longer unemitted):",
            file=sys.stderr,
        )
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nAn entry that outlived its violation pre-authorises a future "
            "one reusing the same name. Regenerate with 'uv run python "
            "scripts/check_declared_event_is_emitted.py --update'.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
