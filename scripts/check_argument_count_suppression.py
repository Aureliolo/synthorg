#!/usr/bin/env python3
"""Pre-push / CI gate: the PLR0913 argument-count cap is closed, not advisory.

``[tool.ruff.lint.pylint] max-args`` only means something if the set of
functions allowed to exceed it is finite and shrinking. Left to ``# noqa``
alone the cap is decorative: the marker is freely addable, so a cap
suppressed hundreds of times reports nothing and prevents nothing.

Where the population comes from
-------------------------------
The candidate set is derived here, from the AST (see
:mod:`_argument_count_sites`), NOT from what ``ruff`` reports. Trusting the
``ruff`` diagnostic set as the whole population fails in two directions:
``ruff`` exempts ``@typing.override`` methods from ``PLR0913``
syntactically, and it never visits a file pruned by ``exclude`` /
``extend-exclude`` / ``.gitignore``. Either way an over-cap function
produces no diagnostic, which an over-trusting gate reads as "clean".

So ``ruff`` classifies, this gate decides who is in scope. Two ``ruff``
passes run over the tree: one with every suppression mechanism neutralised
and the caps forced to match discovery's, one plain. A candidate present in
neither is :attr:`SiteStatus.RULE_EXEMPT` and still needs an approved
baseline entry.

Ruff's answers also bound the parse. Parsing all seven thousand tracked
files costs twenty seconds, and nearly all of them provably hold nothing:
a file ruff walked, did not report at discovery's caps, and which never
mentions the exempting decorator cannot contain a candidate. The first of
those three conditions is why :func:`_visited_files` exists. An inline
``--config exclude=[]`` does not un-prune the walk, so "ruff said nothing"
and "ruff looked" are genuinely different facts, and conflating them would
drop every excluded file from the population without a word.

Invariants
----------
1. **The cap cannot be raised.** ``max-args`` stays at or below
   ``_MAX_ARGS_CEILING``; lowering it is a tightening and always allowed.
   ``max-positional-args`` stays pinned at exactly ``_MAX_POSITIONAL_ARGS``,
   neither raised nor lowered: ``ruff`` defaults it to ``max-args``, so an
   unpinned positional cap silently widens with the other one.
2. **Neither rule can be disabled wholesale**, by ``lint.ignore`` /
   ``extend-ignore`` or by a ``per-file-ignores`` entry, for ``PLR0913`` or
   for ``PLR0917``. Prefix selectors count, so ``"PL"`` is rejected too.
3. **Configuration cannot be relocated out of view.** A ``[tool.ruff]
   extend`` key, or any ``ruff.toml`` / ``.ruff.toml`` / ``pyproject.toml``
   below the root, is rejected: both move the effective config somewhere
   this gate does not read.
4. **Every over-cap function is accounted for.** A per-line marker, or a
   ``ruff`` exemption, is legal only when the site already appears in
   ``scripts/argument_count_suppression_baseline.txt``. A file-level
   ``# ruff: noqa`` blanket is never legal.
5. **The baseline holds no stale entry.** An entry outliving its function
   would silently pre-authorise a future suppression reusing its identity.

There is deliberately no ``# lint-allow:`` opt-out. The baseline is the only
escape, and ``check_baseline_growth.py`` blocks it from growing without an
explicit ``ALLOW_BASELINE_GROWTH=1`` approval.

Baseline
--------
Entries are ``path::qualname::arity``. The qualified name rather than a line
number because this list is long-lived and a ``path:lineno:col`` key would go
stale on any unrelated edit above the marker. The arity because a name alone
is not an identity: without it, deleting a baselined function and writing an
unrelated one under the same name inherits the old approval, and an approved
function can grow from six parameters to sixty with no baseline diff. Two
candidates minting the same key is itself rejected, so one entry can never
authorise two functions.

Regenerate (rare; requires explicit user approval) with ``--update``.

Usage::

    uv run python scripts/check_argument_count_suppression.py
    uv run python scripts/check_argument_count_suppression.py --update

Exit codes:
    0 -- every over-cap function is a baseline entry and the config holds.
    1 -- an unbaselined suppression or exemption, a blanket, a raised cap, a
         disabled rule, or relocated configuration.
    2 -- the scan could not be trusted (bad ``--repo-root``, unreadable or
         malformed baseline, unreadable or unparseable source, ``ruff``
         failing to run, a colliding baseline key, or a stale entry).
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _argument_count_sites import (  # type: ignore[import-not-found]
        Candidate,
        FileScan,
        SiteStatus,
        find_nested_ruff_configs,
        may_be_rule_exempt,
        scan_source,
    )
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._argument_count_sites import (
        Candidate,
        FileScan,
        SiteStatus,
        find_nested_ruff_configs,
        may_be_rule_exempt,
        scan_source,
    )
    from scripts._gate_source import GateSourceError, read_and_parse

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_RULE: Final[str] = "PLR0913"
_POSITIONAL_RULE: Final[str] = "PLR0917"
_BASELINE_REL: Final[str] = "scripts/argument_count_suppression_baseline.txt"

# The cap this gate holds the line at. ``max-args`` may be lowered below it
# (that is a tightening) but never raised above it without editing this
# constant, which is a reviewed change rather than a config tweak.
_MAX_ARGS_CEILING: Final[int] = 8
# Pinned exactly, not as a ceiling: a wide signature is acceptable when every
# argument is named at the call site, but a wide POSITIONAL signature is what
# lets two same-typed arguments swap silently.
_MAX_POSITIONAL_ARGS: Final[int] = 5

# Bounded so a wedged child cannot hang the push. The pooled gate runner kills
# only its own direct workers, so a subprocess grandchild would be orphaned
# rather than reaped, and its serial path (PREPUSH_GATE_JOBS=1) has no batch
# timeout at all.
_RUFF_TIMEOUT_SECONDS: Final[float] = 120.0
# Enough of a crashing child's stderr to diagnose it, not enough to drown the
# hook log in a panic backtrace.
_STDERR_EXCERPT_CHARS: Final[int] = 2000

# The suppression keyword is case-insensitive to ruff, but rule codes are NOT:
# an upper-case keyword is honoured, a lower-case rule code is not. Matching
# the keyword case-insensitively while keeping the code exact mirrors that.
_PER_LINE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s*(?i:noqa)\s*:\s*[A-Za-z0-9, ]*\b" + _RULE + r"\b",
)
_BASELINE_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"^[^:]+\.py::[\w.]+::\d+$")

_BASELINE_HEADER: Final[str] = f"""\
# Functions whose parameter count exceeds [tool.ruff.lint.pylint] max-args.
# Each line is `path::qualname::arity` (POSIX path, dotted qualified name,
# parameter count as {_RULE} counts it) sorted in deterministic order.
#
# scripts/check_argument_count_suppression.py reads this file to allow these
# exact functions. Anything over the cap and NOT in this list fails the
# pre-push hook, and check_baseline_growth.py rejects any commit that makes
# the list longer. The list shrinks monotonically: an entry drops out once
# its function is decomposed back under the cap.
#
# The arity is part of the identity on purpose: widening an already-approved
# signature mints a new key, so it costs a fresh approval rather than riding
# the old one.
#
# There is no per-line opt-out. Adding an entry means regenerating this file,
# which needs explicit user approval:
#   uv run python scripts/check_argument_count_suppression.py --update
"""


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


class RuffInvocationError(Exception):
    """Raised when the ruff subprocess could not be run or understood."""


@dataclass(frozen=True)
class _Site:
    """An over-cap function together with how ruff currently treats it."""

    candidate: Candidate
    status: SiteStatus

    @property
    def key(self) -> str:
        """Return the baseline identity, for a site the baseline can hold.

        Returns:
            The ``path::qualname::arity`` key.

        Raises:
            ValueError: If the site is unsuppressed or blanket-suppressed.
                Neither is baselineable, and minting a plausible key for one
                is precisely the laundering this gate exists to prevent.
        """
        if self.status not in {SiteStatus.PER_LINE, SiteStatus.RULE_EXEMPT}:
            msg = (
                f"{self.candidate.rel}:{self.candidate.lineno}: a "
                f"{self.status} site has no baseline identity"
            )
            raise ValueError(msg)
        # Bound through a local so the dual-import shim (which types the
        # sibling module as Any on the non-package branch) cannot widen this.
        key: str = self.candidate.key
        return key

    def message(self) -> str:
        """Return the human-facing violation message."""
        where = f"{self.candidate.rel}:{self.candidate.lineno}"
        breach = (
            f"{self.candidate.arg_count} arguments"
            if self.candidate.over_arg_cap
            else f"{self.candidate.positional_count} positional arguments"
        )
        what = f"{self.candidate.qualname}() takes {breach}"
        detail = {
            SiteStatus.BLANKET: (
                f"and is suppressed by a file-level '# ruff: noqa' or a "
                f"per-file-ignores entry. A blanket exemption cannot be "
                f"baselined: suppress the one function with a per-line "
                f"'# noqa: {_RULE}', or decompose it"
            ),
            SiteStatus.UNSUPPRESSED: (
                "and is not suppressed at all, so ruff reports it directly. "
                "Decompose it, or bundle the parameters into a params object"
            ),
            SiteStatus.RULE_EXEMPT: (
                f"and is exempt from {_RULE} by decorator, so ruff never "
                f"reports it and a '# noqa' here would itself be dead. It "
                f"still needs a baseline entry, or decompose it"
            ),
            SiteStatus.PER_LINE: (
                f"and carries a per-line marker that is not in "
                f"{_BASELINE_REL}. The list is closed: decompose it, or "
                f"regenerate the baseline with explicit approval"
            ),
        }[self.status]
        return f"{where}: {what} {detail}."


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


def _baseline_path(project_root: Path) -> Path:
    """Return the baseline file location anchored at *project_root*."""
    return project_root / _BASELINE_REL


def _tracked_files(project_root: Path) -> list[str]:
    """Return every tracked path, POSIX-relative to *project_root*.

    Returns:
        The tracked paths, sorted.

    Raises:
        GateSourceError: If ``git`` cannot be run or fails. Unlike a file
            scan that can fall back to ``rglob``, the population itself must
            be exact: a silently narrower list would understate the universe,
            which is the failure this gate exists to prevent.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            check=True,
            capture_output=True,
            cwd=project_root,
            timeout=_RUFF_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        msg = f"could not enumerate tracked files ({type(exc).__name__}: {exc})"
        raise GateSourceError(msg) from exc
    out = result.stdout.decode("utf-8", errors="replace")
    return sorted(p for p in out.split("\0") if p)


# ── ruff invocation ─────────────────────────────────────────────


def _ruff_command() -> list[str]:
    """Return the argv prefix that runs the venv's pinned ruff.

    The console script beside the interpreter, when it exists, rather than
    ``python -m ruff``: both resolve the same pinned ruff out of the same
    environment, but the module form pays a full CPython startup on every
    spawn. The gate spawns three per run and its own test suite drives it
    dozens of times, so the difference is most of a minute across a push.

    Returns:
        The console-script path, or the ``python -m ruff`` fallback for an
        install that has no script (a bare ``pip install --no-scripts``, or a
        vendored ruff imported as a module).
    """
    name = "ruff.exe" if sys.platform == "win32" else "ruff"
    script = Path(sys.executable).parent / name
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "ruff"]


def _ruff_argv(*extra: str) -> list[str]:
    """Return the ruff command line with *extra* appended."""
    return [
        *_ruff_command(),
        "check",
        ".",
        "--select",
        f"{_RULE},{_POSITIONAL_RULE}",
        "--output-format",
        "json",
        *extra,
    ]


def _visited_files(project_root: Path) -> frozenset[str]:
    """Return the paths ruff's own walk reaches, as ruff reports them.

    Asked rather than inferred. An inline ``--config exclude=[]`` does NOT
    un-prune the walk (ruff resolves exclusions during traversal, so a pruned
    directory is never opened whatever the override says), and reimplementing
    the exclusion semantics here would be a second source of truth free to
    drift from the first. ``--show-files`` is ruff answering for itself.

    Returns:
        Project-relative POSIX paths. Empty when ruff printed nothing, which
        makes every file count as pruned and so parsed: slow, never blind.

    Raises:
        RuffInvocationError: If ruff could not be run or exited abnormally.
    """
    try:
        result = subprocess.run(
            [*_ruff_command(), "check", ".", "--show-files"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=project_root,
            timeout=_RUFF_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        msg = f"could not enumerate ruff's files ({type(exc).__name__}: {exc})"
        raise RuffInvocationError(msg) from exc
    if result.returncode not in {0, 1}:
        excerpt = result.stderr.strip()[:_STDERR_EXCERPT_CHARS] or "<no stderr>"
        msg = f"ruff --show-files exited {result.returncode}: {excerpt}"
        raise RuffInvocationError(msg)
    visited: set[str] = set()
    for line in result.stdout.splitlines():
        candidate = Path(line.strip())
        if line.strip() and candidate.is_relative_to(project_root):
            visited.add(candidate.relative_to(project_root).as_posix())
    return frozenset(visited)


def _neutralised_argv(caps: tuple[int, int]) -> list[str]:
    """Return the flags that switch off every way a site could hide.

    The caps are forced rather than inherited so ruff's population and the
    AST's are the same population by construction. Without that, a
    ``pyproject.toml`` configuring a cap above the ceiling would have ruff
    reporting against the wide bar while discovery counted against the narrow
    one, and the difference would be sites the scan never classified.

    Args:
        caps: The ``(max-args, max-positional-args)`` pair discovery uses.

    Returns:
        The extra ruff arguments for the neutralised pass.
    """
    arg_cap, positional_cap = caps
    return [
        "--ignore-noqa",
        "--config",
        "lint.per-file-ignores={}",
        "--config",
        "lint.extend-per-file-ignores={}",
        "--config",
        "exclude=[]",
        "--config",
        "extend-exclude=[]",
        "--config",
        "respect-gitignore=false",
        "--config",
        f"lint.pylint.max-args={arg_cap}",
        "--config",
        f"lint.pylint.max-positional-args={positional_cap}",
    ]


def _run_ruff(
    project_root: Path,
    *,
    extra: list[str],
    label: str,
) -> list[tuple[str, int]]:
    """Return ``(relative_path, lineno)`` for every site ruff reports.

    Args:
        project_root: Directory to run ruff in.
        extra: Extra arguments; :func:`_neutralised_argv` for the pass that
            must see through every suppression, empty for the pass that
            reports what ruff would say on its own.
        label: Which pass this is, for diagnostics.

    Returns:
        One entry per reported diagnostic.

    Raises:
        RuffInvocationError: If ruff cannot be spawned, times out, exits with
            a code other than clean-or-violations, produces output this gate
            cannot parse, or produces no output at all. Every one of those is
            fail-closed: an empty result would otherwise read as "no
            violations".
    """
    try:
        result = subprocess.run(
            _ruff_argv(*extra),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=project_root,
            timeout=_RUFF_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        msg = f"{label} ruff run failed ({type(exc).__name__}: {exc})"
        raise RuffInvocationError(msg) from exc
    # 0 = clean, 1 = violations found. Anything else is ruff itself failing.
    if result.returncode not in {0, 1}:
        excerpt = result.stderr.strip()[:_STDERR_EXCERPT_CHARS] or "<no stderr>"
        msg = f"{label} ruff run exited {result.returncode}: {excerpt}"
        raise RuffInvocationError(msg)
    if result.stderr.strip():
        # Warnings on an otherwise-successful run are how version skew first
        # shows up (a deprecated --config spelling, a schema change). Silently
        # dropping them hides the drift until it eventually breaks the run.
        print(
            f"check_argument_count_suppression: {label} ruff run warned: "
            f"{result.stderr.strip()[:_STDERR_EXCERPT_CHARS]}",
            file=sys.stderr,
        )
    return _parse_ruff_json(result.stdout, project_root, label)


def _parse_ruff_json(
    stdout: str,
    project_root: Path,
    label: str,
) -> list[tuple[str, int]]:
    """Decode ruff JSON output into ``(relative_path, lineno)`` pairs.

    Returns:
        One entry per diagnostic, in ruff's own order.

    Raises:
        RuffInvocationError: If the payload is absent, not the expected
            shape, or names a path outside *project_root*. Blank output is
            NOT "no violations": with ``--output-format json`` ruff emits at
            least ``[]`` whenever it actually ran, so blank means it did not.
            ``python -m ruff`` exits 1 with blank stdout when ruff is not
            importable, which is the same exit code as "violations found".
    """
    if not stdout.strip():
        msg = (
            f"{label} ruff run produced no output; ruff emits at least "
            f"'[]' when it runs, so it did not run at all (is it installed "
            f"for {sys.executable}?)"
        )
        raise RuffInvocationError(msg)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        msg = f"could not parse {label} ruff output: {exc}"
        raise RuffInvocationError(msg) from exc
    if not isinstance(payload, list):
        msg = f"expected a JSON list from ruff, got {type(payload).__name__}"
        raise RuffInvocationError(msg)
    return [_parse_one_diagnostic(item, project_root) for item in payload]


def _parse_one_diagnostic(item: object, project_root: Path) -> tuple[str, int]:
    """Decode one ruff diagnostic.

    Returns:
        Its ``(relative_path, lineno)``.

    Raises:
        RuffInvocationError: If the shape is wrong or the path escapes the
            project root.
    """
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
    return (absolute.relative_to(project_root).as_posix(), row)


# ── scanning ────────────────────────────────────────────────────


def _effective_caps(project_root: Path) -> tuple[int, int]:
    """Return the ``(max-args, max-positional-args)`` the project configures.

    Discovery runs against the CONFIGURED caps rather than this gate's own
    ceiling, so lowering ``max-args`` genuinely tightens the population
    instead of leaving the gate looking for breaches of a wider bar than
    ruff enforces. ``_check_config_pins`` separately refuses a configured
    cap above the ceiling, so the two can never diverge upward.

    Returns:
        The configured pair, falling back to the gate's own constants when a
        value is missing or malformed (the pin check reports that
        separately).
    """
    try:
        _, _, pylint = _load_ruff_tables(project_root)
    except ValueError:
        return (_MAX_ARGS_CEILING, _MAX_POSITIONAL_ARGS)
    max_args = pylint.get("max-args")
    positional = pylint.get("max-positional-args")
    arg_cap = (
        max_args
        if isinstance(max_args, int) and not isinstance(max_args, bool)
        else _MAX_ARGS_CEILING
    )
    positional_cap = (
        positional
        if isinstance(positional, int) and not isinstance(positional, bool)
        else _MAX_POSITIONAL_ARGS
    )
    return (min(arg_cap, _MAX_ARGS_CEILING), min(positional_cap, _MAX_POSITIONAL_ARGS))


def _read_bytes(path: Path) -> bytes:
    """Return the undecoded contents of *path*.

    Returns:
        The raw bytes.

    Raises:
        GateSourceError: If the file cannot be read, so an unreadable file
            fails the scan rather than being silently skipped.
    """
    try:
        return path.read_bytes()
    except OSError as exc:
        msg = f"{path}: could not read source: {exc}"
        raise GateSourceError(msg) from exc


def _needs_parse(
    rel: str,
    path: Path,
    reported_files: frozenset[str],
    visited: frozenset[str],
) -> bool:
    """Whether *rel* could hold a candidate, and so must be parsed.

    A file is provably candidate-free only when all three hold: ruff visited
    it (so it was actually examined), ruff did not report it at discovery's
    caps (so nothing in it is over the bar), and it does not mention the
    exempting decorator (the one thing ruff declines to report). Anything
    else gets parsed. The tests pin each of the three, because getting this
    wrong loses sites silently rather than loudly.

    Returns:
        ``True`` when the file must be parsed to be classified.

    Raises:
        GateSourceError: If the file cannot be read.
    """
    if rel in reported_files or rel not in visited:
        return True
    # Bound through a local so the dual-import shim (which types the sibling
    # module as Any on the non-package branch) cannot widen this.
    exempt: bool = may_be_rule_exempt(_read_bytes(path))
    return exempt


def _scan_files(
    project_root: Path,
    caps: tuple[int, int],
    reported_files: frozenset[str],
    visited: frozenset[str],
) -> dict[str, FileScan]:
    """Return the per-file candidate scan for every tracked file that needs one.

    Args:
        project_root: The tree to scan.
        caps: The ``(max-args, max-positional-args)`` pair a candidate exceeds.
        reported_files: Paths the neutralised ruff pass reported on.
        visited: Paths ruff's own walk reaches.

    Returns:
        A mapping of relative path to its scan, for the files that can hold a
        candidate. A file :func:`_needs_parse` rules out contributes nothing
        and is absent.

    Raises:
        GateSourceError: If a file that must be parsed cannot be read or
            parsed (fail-closed).
    """
    arg_cap, positional_cap = caps
    scans: dict[str, FileScan] = {}
    for rel in _tracked_files(project_root):
        if not rel.endswith(".py"):
            continue
        path = project_root / rel
        if not path.is_file():
            continue
        if not _needs_parse(rel, path, reported_files, visited):
            continue
        text, tree = read_and_parse(path)
        scans[rel] = scan_source(rel, text, tree, arg_cap, positional_cap)
    return scans


def _status_of(
    candidate: Candidate,
    scan: FileScan,
    reported: set[tuple[str, int]],
    unsuppressed: set[tuple[str, int]],
) -> SiteStatus:
    """Classify one candidate against what the two ruff passes reported.

    Returns:
        ``UNSUPPRESSED`` when ruff reports it on its own, ``RULE_EXEMPT``
        when neither pass mentions it, ``PER_LINE`` when the reported line
        carries a marker naming the rule, and ``BLANKET`` otherwise.
    """
    coord = (candidate.rel, candidate.lineno)
    if coord in unsuppressed:
        return SiteStatus.UNSUPPRESSED
    if scan.has_blanket:
        return SiteStatus.BLANKET
    line = (
        scan.lines[candidate.lineno - 1] if candidate.lineno <= len(scan.lines) else ""
    )
    if coord in reported and _PER_LINE_MARKER_RE.search(line):
        return SiteStatus.PER_LINE
    # Not reported by either pass and no file-level blanket: ruff exempts it
    # (an @override decorator) or a per-file-ignores entry covers the rule it
    # breaches. Both are declared, reviewable exemptions rather than a blanket,
    # so the site stays baselineable and lands on the ledger.
    return SiteStatus.RULE_EXEMPT


def _scan(project_root: Path) -> list[_Site]:
    """Return every over-cap function in the tree, classified.

    Returns:
        One :class:`_Site` per over-cap function definition.

    Raises:
        RuffInvocationError: If either ruff pass could not be trusted.
        GateSourceError: If a source file could not be read or parsed.
    """
    caps = _effective_caps(project_root)
    reported = set(
        _run_ruff(
            project_root,
            extra=_neutralised_argv(caps),
            label="neutralised",
        )
    )
    unsuppressed = set(_run_ruff(project_root, extra=[], label="plain"))
    # Ruff runs first because its answers are what bound the parse: which
    # files it flagged, and which it walked at all.
    scans = _scan_files(
        project_root,
        caps,
        frozenset(rel for rel, _ in reported),
        _visited_files(project_root),
    )
    return [
        _Site(
            candidate=candidate,
            status=_status_of(candidate, scan, reported, unsuppressed),
        )
        for scan in scans.values()
        for candidate in scan.candidates
    ]


def _collisions(sites: list[_Site]) -> list[str]:
    """Return a message per baseline key claimed by more than one site.

    A key that is not unique means one approved entry silently authorises
    several functions, including ones added after the approval. The gate
    refuses to operate on a key space it cannot trust rather than quietly
    covering the extras.

    Returns:
        One message per colliding key, sorted.
    """
    by_key: dict[str, list[_Site]] = {}
    for site in sites:
        if site.status in {SiteStatus.PER_LINE, SiteStatus.RULE_EXEMPT}:
            by_key.setdefault(site.key, []).append(site)
    messages: list[str] = []
    for key, group in sorted(by_key.items()):
        if len(group) == 1:
            continue
        lines = ", ".join(
            str(s.candidate.lineno)
            for s in sorted(group, key=lambda s: s.candidate.lineno)
        )
        messages.append(
            f"{key}: claimed by {len(group)} definitions (lines {lines}). One "
            f"baseline entry cannot authorise several functions; rename or "
            f"decompose so each has its own identity."
        )
    return messages


# ── ruff configuration pins ─────────────────────────────────────


def _load_ruff_tables(
    project_root: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Return the ``[tool.ruff]``, ``[...lint]`` and ``[...lint.pylint]`` tables.

    Returns:
        A ``(ruff, lint, pylint)`` triple; any missing table is empty.

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
    tool = payload.get("tool", {})
    ruff = tool.get("ruff", {}) if isinstance(tool, dict) else {}
    ruff_table = ruff if isinstance(ruff, dict) else {}
    lint = ruff_table.get("lint", {})
    lint_table = lint if isinstance(lint, dict) else {}
    pylint = lint_table.get("pylint", {})
    return (ruff_table, lint_table, pylint if isinstance(pylint, dict) else {})


def _disables_rule(codes: object, rule: str) -> bool:
    """Whether *codes* contains an entry that would silence *rule*.

    A prefix silences everything under it, so ``"PL"`` disables ``PLR0913``
    just as surely as the full code does. A non-list value reads as "does not
    disable": ruff rejects a malformed ``ignore`` / ``per-file-ignores`` shape
    at its own config-parse stage, so such a config never reaches a passing
    scan anyway.

    Returns:
        ``True`` when any entry is a prefix of (or equal to) *rule*.
    """
    if not isinstance(codes, list):
        return False
    return any(
        isinstance(code, str) and code and rule.startswith(code) for code in codes
    )


def _check_cap_pins(pylint: dict[str, object]) -> list[str]:
    """Return one message per broken numeric cap pin."""
    problems: list[str] = []
    max_args = pylint.get("max-args")
    # ``bool`` subclasses ``int``, so a stray ``max-args = true`` would pass a
    # bare isinstance check and compare below the ceiling.
    within_ceiling = (
        isinstance(max_args, int)
        and not isinstance(max_args, bool)
        and max_args <= _MAX_ARGS_CEILING
    )
    if not within_ceiling:
        problems.append(
            f"[tool.ruff.lint.pylint] max-args must be an integer at or below "
            f"{_MAX_ARGS_CEILING}, got {max_args!r}. Raising the cap until the "
            f"residue disappears is what this gate exists to stop; lowering it "
            f"is always allowed."
        )
    positional = pylint.get("max-positional-args")
    if positional != _MAX_POSITIONAL_ARGS or isinstance(positional, bool):
        problems.append(
            f"[tool.ruff.lint.pylint] max-positional-args must stay pinned at "
            f"exactly {_MAX_POSITIONAL_ARGS}, got {positional!r}. Ruff defaults "
            f"it to max-args, so an unpinned positional cap widens with it."
        )
    return problems


def _check_rule_reachable(lint: dict[str, object]) -> list[str]:
    """Return one message per config entry that disables a rule tree-wide.

    Only the global keys are rejected. A ``per-file-ignores`` entry is no
    longer a hiding place: candidates come from the AST, so a path-glob
    exemption changes how a site is CLASSIFIED but never whether the gate
    sees it, and every such function still needs a baseline entry. That is
    what lets the framework-shaped Litestar and pytest signatures keep their
    ``PLR0917`` exemptions (a route handler's query surface is its
    parameters, and a fixture graph chooses its own positional count) while
    staying on the ledger.

    A tree-wide ignore is still rejected: it would collapse every candidate
    to ``RULE_EXEMPT`` at once, which is a config change nobody should make
    by accident.

    Returns:
        One message per global ignore covering either rule.
    """
    return [
        f"[tool.ruff.lint] {key} disables {rule} for the whole tree."
        for rule in (_RULE, _POSITIONAL_RULE)
        for key in ("ignore", "extend-ignore")
        if _disables_rule(lint.get(key), rule)
    ]


def _check_config_located(
    ruff: dict[str, object],
    project_root: Path,
    tracked: list[str],
) -> list[str]:
    """Return one message per way the effective config could move out of view."""
    problems: list[str] = []
    if "extend" in ruff:
        problems.append(
            f"[tool.ruff] extend = {ruff['extend']!r} moves configuration into "
            f"another file this gate does not read, where an extend-ignore "
            f"could silence the rule invisibly. Inline the settings instead."
        )
    problems.extend(
        f"{rel}: a ruff config below the repository root overrides the cap for "
        f"its subtree, and the ruff default select does not include the pylint "
        f"family at all. Remove it, or fold its settings into the root config."
        for rel in find_nested_ruff_configs(project_root, tracked)
    )
    return problems


def _check_ruff_pin(project_root: Path) -> list[str]:
    """Return a message when the resolved ruff is not the pinned version.

    What counts as over-cap, and which decorators are exempt, are both ruff
    behaviours. A lockfile drift would redefine them with no other signal, so
    the gate says so rather than silently measuring something new.

    Returns:
        One message when the versions disagree, empty otherwise.
    """
    _, _, _ = _load_ruff_tables(project_root)
    pinned = _pinned_ruff_version(project_root)
    if pinned is None:
        return []
    try:
        result = subprocess.run(
            [*_ruff_command(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=project_root,
            timeout=_RUFF_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired, UnicodeDecodeError:
        return ["could not resolve the ruff version to compare against the pin."]
    resolved = result.stdout.strip().removeprefix("ruff").strip()
    if resolved != pinned:
        message = (
            f"ruff resolves to {resolved!r} but pyproject.toml pins {pinned!r}. "
            f"What counts as over-cap is a ruff behaviour, so a drifted "
            f"version measures something this baseline was not drawn against."
        )
        return [message]
    return []


def _pinned_ruff_version(project_root: Path) -> str | None:
    """Return the ``ruff==<version>`` pin from the dev dependency group.

    Returns:
        The pinned version, or ``None`` when no exact pin is declared.
    """
    try:
        with (project_root / "pyproject.toml").open("rb") as handle:
            payload = tomllib.load(handle)
    except OSError, tomllib.TOMLDecodeError:
        return None
    groups = payload.get("dependency-groups", {})
    if not isinstance(groups, dict):
        return None
    for entries in groups.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str) and entry.startswith("ruff=="):
                return entry.removeprefix("ruff==").strip()
    return None


def _check_config_pins(project_root: Path, tracked: list[str]) -> list[str]:
    """Return one message per broken ruff-configuration invariant.

    Returns:
        An empty list when the caps hold, both rules stay reachable, and the
        effective configuration lives where this gate reads it.

    Raises:
        ValueError: If ``pyproject.toml`` could not be read or parsed.
    """
    ruff, lint, pylint = _load_ruff_tables(project_root)
    return [
        *_check_cap_pins(pylint),
        *_check_rule_reachable(lint),
        *_check_config_located(ruff, project_root, tracked),
        *_check_ruff_pin(project_root),
    ]


# ── baseline ────────────────────────────────────────────────────


def _load_baseline(path: Path) -> set[str]:
    """Return the allowlisted ``path::qualname::arity`` baseline entries.

    Returns:
        The frozen baseline entries (empty when the file is absent).

    Raises:
        ValueError: On malformed or duplicate entries, or an unreadable file,
            so a corrupt baseline fails loud rather than passing a silently
            truncated allowlist.
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
                f"{_BASELINE_REL}:{lineno}: malformed entry (expected "
                f"'path::qualname::arity', got {stripped!r})"
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
    """Sort + write the baselineable *sites* as a baseline file."""
    keys = sorted(
        {
            s.key
            for s in sites
            if s.status in {SiteStatus.PER_LINE, SiteStatus.RULE_EXEMPT}
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_BASELINE_HEADER + "\n".join(keys) + "\n", encoding="utf-8")


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
        tracked = _tracked_files(project_root)
        sites = _scan(project_root)
    except (GateSourceError, RuffInvocationError) as exc:
        print(
            f"check_argument_count_suppression: {exc}\nBaseline left untouched.",
            file=sys.stderr,
        )
        return 2
    collisions = _collisions(sites)
    if collisions:
        for message in collisions:
            print(message, file=sys.stderr)
        print("\nBaseline left untouched.", file=sys.stderr)
        return 2
    del tracked
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
    kept = [
        s for s in sites if s.status in {SiteStatus.PER_LINE, SiteStatus.RULE_EXEMPT}
    ]
    rejected = [s for s in sites if s not in kept]
    print(f"Wrote {len({s.key for s in kept})} entries to {_BASELINE_REL}.")
    for site in sorted(rejected, key=lambda s: (s.candidate.rel, s.candidate.lineno)):
        print(f"  omitted (not baselineable): {site.message()}")
    if rejected:
        print(
            f"\n{len(rejected)} over-cap site(s) are not baseline-eligible and "
            f"were omitted; the gate still fails on them.",
            file=sys.stderr,
        )
    return 0


def _report_stale(stale: list[str]) -> None:
    """Print the stale-baseline diagnosis for *stale* entries."""
    for entry in stale:
        print(f"{_BASELINE_REL}: stale baseline entry {entry}", file=sys.stderr)
    print(
        f"\n{len(stale)} baseline entr"
        f"{'y' if len(stale) == 1 else 'ies'} no longer match an over-cap "
        "function. An entry that outlives its function would silently "
        "pre-authorise a future suppression reusing the same identity. Remove "
        "the stale line(s), or regenerate with 'uv run python scripts/"
        "check_argument_count_suppression.py --update'.",
        file=sys.stderr,
    )


def cmd_scan(project_root: Path) -> int:
    """Check the config pins and every over-cap site against the baseline.

    Returns:
        ``0`` when clean, ``1`` on a broken pin or an unaccounted site, ``2``
        when the scan itself could not be trusted.
    """
    try:
        tracked = _tracked_files(project_root)
        pin_problems = _check_config_pins(project_root, tracked)
        baseline = _load_baseline(_baseline_path(project_root))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except GateSourceError as exc:
        print(f"check_argument_count_suppression: {exc}", file=sys.stderr)
        return 2
    try:
        sites = _scan(project_root)
    except (GateSourceError, RuffInvocationError) as exc:
        print(f"check_argument_count_suppression: {exc}", file=sys.stderr)
        return 2

    collisions = _collisions(sites)
    if collisions:
        for message in collisions:
            print(message, file=sys.stderr)
        return 2

    live_keys = {
        s.key
        for s in sites
        if s.status in {SiteStatus.PER_LINE, SiteStatus.RULE_EXEMPT}
    }
    stale = sorted(baseline - live_keys)
    if stale:
        _report_stale(stale)
        return 2

    violations = [
        s
        for s in sites
        if s.status not in {SiteStatus.PER_LINE, SiteStatus.RULE_EXEMPT}
        or s.key not in baseline
    ]
    if not pin_problems and not violations:
        return 0
    # Both streams carry findings for an exit-1 run, so every finding goes to
    # stdout and stderr keeps only the summary. A pin-only failure would
    # otherwise print nothing to stdout at all, making a real violation
    # indistinguishable by stream from a broken scan.
    for problem in pin_problems:
        print(problem)
    for site in sorted(violations, key=lambda s: (s.candidate.rel, s.candidate.lineno)):
        print(site.message())
    print(
        f"\n{len(pin_problems)} configuration problem(s) and "
        f"{len(violations)} unaccounted over-cap function(s). The cap is "
        f"closed by design: there is no per-line opt-out, and the baseline "
        f"may only shrink.",
        file=sys.stderr,
    )
    return 1


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
