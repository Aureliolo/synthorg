#!/usr/bin/env python3
"""Pre-push / CI gate: the PLR0913 argument-count cap is closed, not advisory.

``[tool.ruff.lint.pylint] max-args`` only means something if the set of
functions allowed to exceed it is finite and shrinking. Left to ``# noqa``
alone the cap is decorative: the marker is freely addable, so a cap
suppressed hundreds of times reports nothing and prevents nothing.

This gate closes every route around the cap:

1. **The cap itself cannot be raised.** ``max-args`` must stay at or below
   ``_MAX_ARGS_CEILING`` and ``max-positional-args`` must stay pinned at
   ``_MAX_POSITIONAL_ARGS``. Lowering either is always allowed. Quietly
   raising ``max-args`` until the residue disappears is the primary failure
   mode this exists to stop, and the positional pin is load-bearing on its
   own: ruff defaults ``max-positional-args`` to whatever ``max-args`` is,
   so an unpinned positional cap silently follows the wider one.
2. **PLR0913 cannot be disabled wholesale**, by ``lint.ignore`` /
   ``lint.extend-ignore`` or by a ``per-file-ignores`` entry. (Deleting
   ``"PL"`` from ``lint.select`` is not guarded here: that disables twenty
   other pylint rules across the whole tree, which is loud rather than
   stealthy.)
3. **Every over-cap function carries a per-line marker**, never a file-level
   ``# ruff: noqa`` blanket. ``RUF100`` does not police file-level
   directives, so a stale blanket is otherwise invisible.
4. **Every per-line marker is in the baseline.** A site absent from
   ``scripts/argument_count_suppression_baseline.txt`` fails, and the
   generic ``check_baseline_growth.py`` pre-commit hook keeps that file
   shrink-only, so a new suppression cannot land without an explicit,
   approved regeneration.

There is deliberately no ``# lint-allow:`` opt-out. The baseline is the only
escape, and it is the one a human has to approve.

Detection
---------
Ruff answers the "is this function over the cap" question, not this gate:
reimplementing the count (``self`` / ``cls`` exclusion, ``*`` and ``**``
handling, ``@overload``, decorators) would drift from ruff's own semantics
the moment either side changed. The gate runs ruff twice over the tree:

* with ``--ignore-noqa`` and ``lint.per-file-ignores={}``, giving every
  over-cap function regardless of how it is suppressed;
* plain, giving the ones ruff itself would already report.

A site in the first set but not the second is suppressed. Reading the
reported line then says how: a ``# noqa`` naming PLR0913 is a per-line
marker (baseline-governed); anything else is a blanket (rejected).

Baseline
--------
``scripts/argument_count_suppression_baseline.txt`` lists the per-line
suppressed sites as ``path::qualname`` (``pkg/mod.py::Class.method``). The
key is the qualified name rather than a line number on purpose: this list is
long-lived, and a ``path:lineno:col`` key would go stale on any unrelated
edit above one of the markers, turning every neighbouring PR into a baseline
regeneration. Regenerate (rare; requires explicit user approval) with
``--update``.

Usage::

    uv run python scripts/check_argument_count_suppression.py
    uv run python scripts/check_argument_count_suppression.py --update

Exit codes:
    0 -- the cap pins hold and every over-cap site is a baseline entry.
    1 -- a new or unbaselined suppression, or a raised cap.
    2 -- configuration error (bad ``--repo-root``, unreadable or malformed
         baseline, a source file that could not be read or parsed, ruff
         failing to run, or a stale baseline entry -- fail-closed).
"""

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, override

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_RULE: Final[str] = "PLR0913"
_BASELINE_REL: Final[str] = "scripts/argument_count_suppression_baseline.txt"

# The cap this gate holds the line at. ``max-args`` may be lowered below it
# (that is a tightening) but never raised above it without editing this
# constant, which is a reviewed change rather than a config tweak.
_MAX_ARGS_CEILING: Final[int] = 8
# Pinned exactly, not as a ceiling: a wide signature is acceptable when every
# argument is named at the call site, but a wide POSITIONAL signature is what
# lets two same-typed arguments swap silently.
_MAX_POSITIONAL_ARGS: Final[int] = 5

# A per-line suppression naming the rule, in any of ruff's accepted
# spellings, with or without trailing rationale text after a double dash.
_PER_LINE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s*noqa\s*:\s*[A-Z0-9, ]*\b" + _RULE + r"\b",
)
_BASELINE_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"^[^:]+\.py::[\w.]+$")

_BASELINE_HEADER: Final[str] = f"""\
# Functions exceeding [tool.ruff.lint.pylint] max-args that carry a per-line
# `# noqa: {_RULE}` marker. Each line is `path::qualname` (POSIX path, dotted
# qualified name) sorted in deterministic order.
#
# scripts/check_argument_count_suppression.py reads this file to allow the
# suppression at these exact functions. A marker NOT in this list fails the
# pre-push hook, and check_baseline_growth.py rejects any commit that makes
# the list longer. The list shrinks monotonically: an entry drops out once
# its function is decomposed back under the cap.
#
# There is no per-line opt-out. Adding a suppression means regenerating this
# file, which needs explicit user approval:
#   uv run python scripts/check_argument_count_suppression.py --update
"""


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


class RuffInvocationError(Exception):
    """Raised when the ruff subprocess could not be run or understood."""


class Suppression(StrEnum):
    """How an over-cap function is currently kept out of ruff's output."""

    PER_LINE = "per-line"
    BLANKET = "blanket"
    NONE = "none"


@dataclass(frozen=True)
class _Site:
    """One function whose parameter count exceeds ``max-args``."""

    rel: str
    lineno: int
    qualname: str
    suppression: Suppression

    def __post_init__(self) -> None:
        """Reject coordinates the scan can never legally produce.

        Surfaces a scan-loop bug immediately rather than letting an invalid
        key reach the baseline, where it would only fail much later on
        round-trip.

        Raises:
            ValueError: If ``rel`` or ``qualname`` is empty, or ``lineno`` < 1.
        """
        if not self.rel:
            msg = "rel must not be empty"
            raise ValueError(msg)
        if not self.qualname:
            msg = f"{self.rel}:{self.lineno}: qualname must not be empty"
            raise ValueError(msg)
        if self.lineno < 1:
            msg = f"lineno must be >= 1, got {self.lineno}"
            raise ValueError(msg)

    def baseline_key(self) -> str:
        """Return the ``path::qualname`` baseline identity for this site."""
        return f"{self.rel}::{self.qualname}"

    def message(self) -> str:
        """Return the human-facing violation message."""
        if self.suppression is Suppression.BLANKET:
            detail = (
                f"suppressed by a file-level '# ruff: noqa' or a "
                f"per-file-ignores entry. A blanket exemption cannot be "
                f"baselined; suppress the one function with a per-line "
                f"'# noqa: {_RULE}' or decompose it"
            )
        elif self.suppression is Suppression.NONE:
            detail = (
                "exceeds max-args and is not suppressed at all "
                "(ruff reports it directly). Decompose it, or bundle the "
                "parameters into a params object"
            )
        else:
            detail = (
                "carries a per-line marker that is not in "
                f"{_BASELINE_REL}. The suppression list is closed: decompose "
                "the function, or regenerate the baseline with explicit "
                "approval"
            )
        return f"{self.rel}:{self.lineno}: {self.qualname}() {detail}."


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
    return project_root / _BASELINE_REL


# ── ruff invocation ─────────────────────────────────────────────


def _run_ruff(project_root: Path, *, neutralise: bool) -> list[tuple[str, int]]:
    """Return ``(relative_path, lineno)`` for every PLR0913 site ruff reports.

    With *neutralise* the scan ignores both ``# noqa`` directives and the
    project's ``per-file-ignores``, so it reports every over-cap function
    whether or not it is currently suppressed. Without it, the scan is what
    ruff would report on its own.

    Args:
        project_root: Directory to run ruff in.
        neutralise: Whether to disable the suppression mechanisms.

    Returns:
        One entry per reported diagnostic, paths relative to *project_root*.

    Raises:
        RuffInvocationError: If ruff cannot be spawned, exits with a code
            other than "clean" or "violations found", or emits output this
            gate cannot parse. Every one of those is a fail-closed condition:
            an empty result would otherwise read as "no violations".
    """
    argv = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        ".",
        "--select",
        _RULE,
        "--output-format",
        "json",
    ]
    if neutralise:
        argv += ["--ignore-noqa", "--config", "lint.per-file-ignores={}"]
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=project_root,
        )
    except OSError as exc:
        msg = f"could not run ruff ({type(exc).__name__}: {exc})"
        raise RuffInvocationError(msg) from exc
    # 0 = clean, 1 = violations found. Anything else is ruff itself failing.
    if result.returncode not in {0, 1}:
        msg = (
            f"ruff exited {result.returncode}: {result.stderr.strip() or '<no stderr>'}"
        )
        raise RuffInvocationError(msg)
    return _parse_ruff_json(result.stdout, project_root)


def _parse_ruff_json(stdout: str, project_root: Path) -> list[tuple[str, int]]:
    """Decode ruff's JSON output into ``(relative_path, lineno)`` pairs.

    Returns:
        One entry per diagnostic, in ruff's own order.

    Raises:
        RuffInvocationError: If the payload is not the expected shape, or a
            reported path lies outside *project_root*.
    """
    if not stdout.strip():
        return []
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        msg = f"could not parse ruff JSON output: {exc}"
        raise RuffInvocationError(msg) from exc
    if not isinstance(payload, list):
        msg = f"expected a JSON list from ruff, got {type(payload).__name__}"
        raise RuffInvocationError(msg)
    sites: list[tuple[str, int]] = []
    for item in payload:
        if not isinstance(item, dict):
            msg = f"expected a JSON object per diagnostic, got {item!r}"
            raise RuffInvocationError(msg)
        filename = item.get("filename")
        location = item.get("location")
        if not isinstance(filename, str) or not isinstance(location, dict):
            msg = f"diagnostic missing filename/location: {item!r}"
            raise RuffInvocationError(msg)
        row = location.get("row")
        if not isinstance(row, int):
            msg = f"diagnostic has a non-integer row: {item!r}"
            raise RuffInvocationError(msg)
        absolute = Path(filename)
        if not absolute.is_relative_to(project_root):
            msg = f"ruff reported a path outside the project root: {filename}"
            raise RuffInvocationError(msg)
        sites.append((absolute.relative_to(project_root).as_posix(), row))
    return sites


# ── qualified-name resolution ───────────────────────────────────


class _QualnameIndex(ast.NodeVisitor):
    """Maps every line ruff can anchor a function diagnostic to its qualname.

    Both the ``def`` line and each decorator line are recorded: ruff anchors
    ``PLR0913`` at the function name, but a decorated definition can report
    against the decorator instead, and guessing wrong would silently drop a
    site from the scan.
    """

    def __init__(self) -> None:
        self._stack: list[str] = []
        self.by_line: dict[int, str] = {}

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._stack.append(node.name)
        qualname = ".".join(self._stack)
        self.by_line[node.lineno] = qualname
        for decorator in node.decorator_list:
            self.by_line[decorator.lineno] = qualname
        self.generic_visit(node)
        self._stack.pop()

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Record a synchronous function and descend into its body."""
        self._visit_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Record an async function and descend into its body."""
        self._visit_function(node)

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Push the class name so its methods qualify as ``Class.method``."""
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()


@dataclass(frozen=True)
class _FileIndex:
    """The per-file lookups the classification pass needs."""

    lines: tuple[str, ...]
    qualnames: dict[int, str]


def _index_file(path: Path) -> _FileIndex:
    """Read and index one source file.

    Returns:
        Its physical lines plus the line-to-qualname map.

    Raises:
        GateSourceError: If the file cannot be read or parsed (fail-closed).
    """
    text, tree = read_and_parse(path)
    index = _QualnameIndex()
    index.visit(tree)
    return _FileIndex(lines=tuple(text.splitlines()), qualnames=index.by_line)


def _classify(
    project_root: Path,
    reported: list[tuple[str, int]],
    unsuppressed: set[tuple[str, int]],
) -> list[_Site]:
    """Resolve each reported diagnostic to a named, classified site.

    Args:
        project_root: Directory the relative paths are anchored at.
        reported: Every over-cap function, suppression neutralised.
        unsuppressed: The subset ruff reports without any neutralisation.

    Returns:
        One :class:`_Site` per diagnostic.

    Raises:
        GateSourceError: If a reported file cannot be read or parsed, or a
            reported line resolves to no function at all (which would mean
            the qualname index and ruff disagree about the tree).
    """
    cache: dict[str, _FileIndex] = {}
    sites: list[_Site] = []
    for rel, lineno in reported:
        index = cache.get(rel)
        if index is None:
            index = _index_file(project_root / rel)
            cache[rel] = index
        qualname = index.qualnames.get(lineno)
        if qualname is None:
            msg = (
                f"{rel}:{lineno}: ruff reported {_RULE} at a line that "
                f"resolves to no function definition"
            )
            raise GateSourceError(msg)
        sites.append(
            _Site(
                rel=rel,
                lineno=lineno,
                qualname=qualname,
                suppression=_suppression_of(
                    index, lineno, (rel, lineno) in unsuppressed
                ),
            )
        )
    return sites


def _suppression_of(
    index: _FileIndex,
    lineno: int,
    is_unsuppressed: bool,  # noqa: FBT001
) -> Suppression:
    """Classify how the site at *lineno* is kept out of ruff's plain output.

    Returns:
        ``NONE`` when ruff reports it anyway, ``PER_LINE`` when the reported
        line carries a ``# noqa`` naming the rule, and ``BLANKET`` otherwise
        (a file-level ``# ruff: noqa`` or a ``per-file-ignores`` entry).
    """
    if is_unsuppressed:
        return Suppression.NONE
    line = index.lines[lineno - 1] if lineno <= len(index.lines) else ""
    if _PER_LINE_MARKER_RE.search(line):
        return Suppression.PER_LINE
    return Suppression.BLANKET


def _scan(project_root: Path) -> list[_Site]:
    """Return every over-cap function in the tree, classified.

    Returns:
        One :class:`_Site` per over-cap function definition.

    Raises:
        RuffInvocationError: If ruff could not be run or understood.
        GateSourceError: If a reported source file could not be read.
    """
    reported = _run_ruff(project_root, neutralise=True)
    unsuppressed = set(_run_ruff(project_root, neutralise=False))
    return _classify(project_root, reported, unsuppressed)


# ── ruff configuration pins ─────────────────────────────────────


def _load_pylint_config(project_root: Path) -> tuple[dict[str, object], ...]:
    """Return the ``[tool.ruff.lint]`` and ``[tool.ruff.lint.pylint]`` tables.

    Returns:
        A ``(lint, pylint)`` pair; either is empty when absent.

    Raises:
        ValueError: If ``pyproject.toml`` is missing or unparseable, so a
            broken config fails the gate rather than skipping the pins.
    """
    path = project_root / "pyproject.toml"
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"could not read pyproject.toml ({type(exc).__name__}: {exc})"
        raise ValueError(msg) from exc
    node: object = payload
    for key in ("tool", "ruff", "lint"):
        node = node.get(key, {}) if isinstance(node, dict) else {}
    lint = node if isinstance(node, dict) else {}
    pylint = lint.get("pylint", {})
    return (lint, pylint if isinstance(pylint, dict) else {})


def _disables_rule(codes: object) -> bool:
    """Whether *codes* contains an entry that would silence the rule.

    A prefix silences everything under it, so ``"PL"`` disables ``PLR0913``
    just as surely as the full code does.

    Returns:
        ``True`` when any entry is a prefix of (or equal to) the rule code.
    """
    if not isinstance(codes, list):
        return False
    return any(
        isinstance(code, str) and code and _RULE.startswith(code) for code in codes
    )


def _check_config_pins(project_root: Path) -> list[str]:
    """Return one message per broken ruff-configuration pin.

    Returns:
        An empty list when the cap pins hold and nothing disables the rule.

    Raises:
        ValueError: If ``pyproject.toml`` could not be read or parsed.
    """
    lint, pylint = _load_pylint_config(project_root)
    problems: list[str] = []
    max_args = pylint.get("max-args")
    if not isinstance(max_args, int) or max_args > _MAX_ARGS_CEILING:
        problems.append(
            f"[tool.ruff.lint.pylint] max-args must be an integer at or below "
            f"{_MAX_ARGS_CEILING}, got {max_args!r}. Raising the cap until the "
            f"residue disappears is exactly what this gate exists to stop; "
            f"lowering it is always allowed."
        )
    positional = pylint.get("max-positional-args")
    if positional != _MAX_POSITIONAL_ARGS:
        problems.append(
            f"[tool.ruff.lint.pylint] max-positional-args must stay pinned at "
            f"{_MAX_POSITIONAL_ARGS}, got {positional!r}. Ruff defaults it to "
            f"max-args, so an unpinned positional cap silently widens with it."
        )
    problems.extend(
        f"[tool.ruff.lint] {key} disables {_RULE} for the whole tree."
        for key in ("ignore", "extend-ignore")
        if _disables_rule(lint.get(key))
    )
    for key in ("per-file-ignores", "extend-per-file-ignores"):
        table = lint.get(key)
        if not isinstance(table, dict):
            continue
        problems.extend(
            f"[tool.ruff.lint.{key}] entry {pattern!r} disables {_RULE}. A "
            f"path-glob exemption is a blanket wearing config clothes; "
            f"suppress the individual functions instead."
            for pattern, codes in table.items()
            if _disables_rule(codes)
        )
    return problems


# ── baseline ────────────────────────────────────────────────────


def _load_baseline(path: Path) -> set[str]:
    """Return the set of allowlisted ``path::qualname`` baseline entries.

    Returns:
        The frozen baseline entries (empty when the file is absent).

    Raises:
        ValueError: On malformed/duplicate entries or an unreadable file, so
            a corrupt baseline fails the gate loud rather than passing a
            silently-truncated allowlist.
    """
    if not path.exists():
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{_BASELINE_REL}: cannot read baseline ({type(exc).__name__}: {exc})"
        raise ValueError(msg) from exc
    entries: set[str] = set()
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _BASELINE_ENTRY_RE.match(stripped):
            errors.append(
                f"{_BASELINE_REL}:{lineno}: malformed entry "
                f"(expected 'path::qualname', got {stripped!r})"
            )
            continue
        if stripped in entries:
            errors.append(f"{_BASELINE_REL}:{lineno}: duplicate entry {stripped!r}")
            continue
        entries.add(stripped)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        msg = (
            f"{_BASELINE_REL}: baseline failed validation "
            f"({len(errors)} error{'s' if len(errors) != 1 else ''}); "
            f"regenerate with 'uv run python scripts/"
            f"check_argument_count_suppression.py --update' or fix by hand."
        )
        raise ValueError(msg)
    return entries


def _write_baseline(sites: list[_Site], path: Path) -> None:
    """Sort + write the per-line-suppressed *sites* as a baseline file."""
    keys = sorted(
        {s.baseline_key() for s in sites if s.suppression is Suppression.PER_LINE}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_BASELINE_HEADER + "\n".join(keys) + "\n", encoding="utf-8")


# ── commands ────────────────────────────────────────────────────


def cmd_update(project_root: Path) -> int:
    """Regenerate the baseline from the current tree.

    Returns:
        ``0`` on success, ``2`` if the scan or the write failed.
    """
    try:
        sites = _scan(project_root)
    except (GateSourceError, RuffInvocationError) as exc:
        print(f"check_argument_count_suppression: {exc}", file=sys.stderr)
        return 2
    baseline_path = _baseline_path(project_root)
    try:
        _write_baseline(sites, baseline_path)
    except OSError as exc:
        print(
            f"check_argument_count_suppression: could not write baseline "
            f"{baseline_path} ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return 2
    kept = sum(1 for s in sites if s.suppression is Suppression.PER_LINE)
    rejected = len(sites) - kept
    print(f"Wrote {kept} entries to {_BASELINE_REL}.", file=sys.stderr)
    if rejected:
        print(
            f"{rejected} over-cap site(s) are NOT baseline-eligible (blanket "
            f"or unsuppressed) and were omitted; the gate still fails on "
            f"them.",
            file=sys.stderr,
        )
    return 0


def _report_stale(stale: list[str]) -> None:
    """Print the stale-baseline diagnosis for *stale* entries."""
    for entry in stale:
        print(f"{_BASELINE_REL}: stale baseline entry {entry}", file=sys.stderr)
    print(
        f"\n{len(stale)} baseline entr"
        f"{'y' if len(stale) == 1 else 'ies'} no longer match a suppressed "
        "function. An entry that outlives its function would silently "
        "pre-authorise a future suppression reusing the same name. Remove "
        "the stale line(s), or regenerate with 'uv run python scripts/"
        "check_argument_count_suppression.py --update'.",
        file=sys.stderr,
    )


def cmd_scan(project_root: Path) -> int:
    """Check the config pins and the suppression list against the baseline.

    Returns:
        ``0`` when clean, ``1`` on a broken pin or a new suppression, ``2``
        on a read/parse, ruff, or stale-baseline error.
    """
    try:
        pin_problems = _check_config_pins(project_root)
        baseline = _load_baseline(_baseline_path(project_root))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        sites = _scan(project_root)
    except (GateSourceError, RuffInvocationError) as exc:
        print(f"check_argument_count_suppression: {exc}", file=sys.stderr)
        return 2

    live_keys = {
        s.baseline_key() for s in sites if s.suppression is Suppression.PER_LINE
    }
    stale = sorted(baseline - live_keys)
    if stale:
        _report_stale(stale)
        return 2

    violations = [
        s
        for s in sites
        if s.suppression is not Suppression.PER_LINE or s.baseline_key() not in baseline
    ]
    if not pin_problems and not violations:
        return 0
    for problem in pin_problems:
        print(problem, file=sys.stderr)
    for site in sorted(violations, key=lambda s: (s.rel, s.lineno)):
        print(site.message())
    print(
        f"\n{len(pin_problems)} configuration problem(s) and "
        f"{len(violations)} unbaselined argument-count suppression(s). The "
        f"cap is closed by design: there is no per-line opt-out, and the "
        f"baseline may only shrink.",
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
