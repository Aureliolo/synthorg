#!/usr/bin/env python3
"""Pre-commit gate: forbid new ``logger.exception(..., error=str(exc))`` sites.

The pattern ``logger.exception(EVENT, ..., error=str(exc))`` is a known
secret-exfiltration vector on credential-handling code paths (SEC-1 /
audit finding 90):

* ``logger.exception`` attaches a full Python traceback; structlog
  serialises frame-local variables into the event, so any in-scope
  ``client_secret`` / ``refresh_token`` / Fernet ciphertext in the
  exception frame leaks to logs.
* ``str(exc)`` on ``httpx.HTTPStatusError`` frequently embeds the
  POST body or response body, which carries the credentials that
  triggered the failure.

This gate walks each file's AST and refuses any new site.  The
grandfathered population at SEC-1 landing is recorded in
``scripts/_logger_exception_baseline.json`` as a per-file *list of
stable locations* (``lineno``, ``col_offset``) so already-grandfathered
callers don't block unrelated commits, while a developer swapping one
grandfathered site for a new one elsewhere in the same file is still
caught: the new ``(lineno, col_offset)`` is absent from the baseline.

What we match
-------------

The matcher is deliberately broader than ``logger.exception`` to cover
every idiom seen in the tree:

* ``logger.exception(..., error=str(exc))``
* ``self._logger.exception(..., error=str(exc))``
* ``audit_logger.exception(..., error=str(exc))``
* ``error=str(exc.args[0])`` / ``error=str(self._inner)``

Specifically, we flag a call when *all* of the following hold:

1. The callee is an ``Attribute`` whose terminal attribute is
   ``exception`` (i.e. ``<anything>.exception(...)``).
2. The receiver is either a bare ``Name`` whose identifier contains
   ``logger``, *or* an ``Attribute`` whose terminal attribute contains
   ``logger`` (the typical ``self._logger`` / ``self.audit_logger``
   shape).
3. One keyword argument has ``arg == "error"`` and ``value`` is a
   ``Call`` to the builtin ``str`` with a single positional argument
   that is a ``Name``, ``Attribute``, or ``Subscript`` (covering
   ``str(exc)``, ``str(self._inner)``, ``str(exc.args[0])``).

To convert a grandfathered site, replace::

    logger.exception(EVENT, ..., error=str(exc))

with::

    logger.warning(
        EVENT,
        ...,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )

(``from synthorg.observability.redaction import safe_error_description``)
then re-run this script with ``--refresh-baseline`` to shrink the
baseline.  The baseline can only shrink through this path; it can never
grow without an explicit SEC review.

Usage::

    python scripts/check_logger_exception_str_exc.py <file>...     # pre-commit
    python scripts/check_logger_exception_str_exc.py --scan-all    # CI / tests
    python scripts/check_logger_exception_str_exc.py --refresh-baseline
"""

import argparse
import ast
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
_BASELINE_PATH = Path(__file__).resolve().parent / "_logger_exception_baseline.json"

_LOGGER_METHODS: frozenset[str] = frozenset({"exception"})
"""Which ``<receiver>.<method>(...)`` names are covered by this gate.

We only gate ``exception`` because it is the only log method that
attaches a Python traceback by default. ``logger.warning`` /
``logger.error`` do not attach traceback, so ``error=str(exc)`` in
those calls is a less severe concern handled at each callsite.
"""

_BASELINE_ENTRY_LEN = 2
"""Length of each on-disk ``[lineno, col_offset]`` baseline entry."""


def _is_logger_receiver(value: ast.expr) -> bool:
    """Return ``True`` if *value* looks like a logger binding.

    Matches bare names (``logger``, ``audit_logger``) as well as
    attribute chains whose terminal attribute contains ``logger``
    (``self._logger``, ``self.audit_logger``, ``cls.logger``, ...).
    """
    if isinstance(value, ast.Name):
        return "logger" in value.id
    if isinstance(value, ast.Attribute):
        return "logger" in value.attr
    return False


class _LoggerExceptionFinder(ast.NodeVisitor):
    """Locate ``<logger>.<method>(..., error=str(exc_like))`` call sites.

    Attributes:
        hits: Tuples of ``(lineno, col_offset)`` for each match.
    """

    def __init__(self) -> None:
        self.hits: list[tuple[int, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Match ``<logger>.<method>(...)`` calls with ``error=str(exc_like)``."""
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _LOGGER_METHODS
            and _is_logger_receiver(func.value)
            and _has_error_str_exc_kwarg(node.keywords)
        ):
            self.hits.append((node.lineno, node.col_offset))
        self.generic_visit(node)


def _has_error_str_exc_kwarg(keywords: Iterable[ast.keyword]) -> bool:
    """Return ``True`` if any keyword is ``error=str(<exc_like>)``.

    ``<exc_like>`` is ``ast.Name`` (``str(exc)``), ``ast.Attribute``
    (``str(self._inner)``), or ``ast.Subscript`` (``str(exc.args[0])``):
    all forms that could carry credential material through ``str``.
    """
    for kw in keywords:
        if kw.arg != "error":
            continue
        value = kw.value
        if not isinstance(value, ast.Call):
            continue
        if not isinstance(value.func, ast.Name) or value.func.id != "str":
            continue
        if len(value.args) != 1:
            continue
        arg = value.args[0]
        if isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)):
            return True
    return False


class InspectionError(RuntimeError):
    """A source file could not be parsed or read for AST inspection.

    Raised from :func:`_scan_file` instead of silently returning "no
    hits" so a bad file fails the gate closed -- the alternative would
    let a deliberately-unparseable file sneak an unsafe site past CI.
    """


def _scan_file(path: Path) -> list[tuple[int, int]]:
    """Return the sorted list of ``(lineno, col_offset)`` hits in *path*.

    Raises:
        InspectionError: If the file cannot be read or parsed. The
            caller MUST surface this as a gate violation; skipping
            unparseable files would let an attacker ship a payload
            that scanners cannot inspect.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        # PEP 758's no-parens form is only valid *without* an ``as``
        # binding; mixing ``as exc`` with the comma list is a grammar
        # error under Python 3.14, so the parens are required here.
        msg = f"failed to read {path}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        msg = f"failed to parse {path}: SyntaxError at line {exc.lineno}: {exc.msg}"
        raise InspectionError(msg) from exc
    finder = _LoggerExceptionFinder()
    finder.visit(tree)
    return sorted(finder.hits)


def _iter_source_files() -> Iterable[Path]:
    """Walk ``src/synthorg/`` for ``.py`` files."""
    yield from sorted(_SRC_ROOT.rglob("*.py"))


def _load_baseline() -> dict[str, set[tuple[int, int]]]:
    """Load the per-file baseline location map.

    The on-disk format is ``{"locations": {<rel-path>: [[lineno, col], ...]}}``.
    Older count-only baselines are silently treated as empty so the
    transition to location-based tracking fails closed (any existing
    hit becomes a violation, prompting a ``--refresh-baseline`` that
    re-captures the tree). Historic count payloads without
    ``locations`` intentionally read as no baseline at all.
    """
    if not _BASELINE_PATH.exists():
        return {}
    with _BASELINE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    locations = data.get("locations", {})
    if not isinstance(locations, dict):
        return {}
    result: dict[str, set[tuple[int, int]]] = {}
    for key, entries in locations.items():
        if not isinstance(entries, list):
            continue
        result[str(key)] = {
            (int(entry[0]), int(entry[1]))
            for entry in entries
            if isinstance(entry, list) and len(entry) == _BASELINE_ENTRY_LEN
        }
    return result


def _validate_baseline_payload(
    locations: dict[str, list[tuple[int, int]]],
) -> list[str]:
    """Return validation errors for *locations* (empty = valid).

    Checks:
    1. Every key resolves to an existing ``.py`` file under ``_REPO_ROOT``.
    2. No key has a duplicate ``(lineno, col_offset)`` pair -- the AST
       walker emits each node at most once, so duplicates indicate a
       generator bug or hand-edit.
    3. All ``lineno`` / ``col_offset`` values are non-negative integers
       and ``lineno`` is at least 1 (lines are 1-indexed).
    """
    errors: list[str] = []
    for key, entries in locations.items():
        abs_path = (_REPO_ROOT / key).resolve()
        if not abs_path.exists() or abs_path.suffix != ".py":
            errors.append(f"baseline key does not resolve to a .py file: {key}")
            continue
        seen: set[tuple[int, int]] = set()
        for lineno, col in entries:
            if lineno < 1 or col < 0:
                errors.append(f"invalid coordinates in {key}: line={lineno} col={col}")
            if (lineno, col) in seen:
                errors.append(
                    f"duplicate baseline entry in {key}: line={lineno} col={col}"
                )
            seen.add((lineno, col))
    return errors


def _current_commit_sha() -> str:
    """Return the short HEAD SHA, or ``"unknown"`` if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(_REPO_ROOT),
        )
    except subprocess.CalledProcessError, FileNotFoundError, OSError:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _save_baseline(locations: dict[str, list[tuple[int, int]]]) -> None:
    """Write a refreshed baseline snapshot in location-list form.

    Enforces stable sort order (file keys alphabetical, entries sorted
    by ``(lineno, col_offset)``) so baselines diff cleanly. Validates
    the payload against :func:`_validate_baseline_payload` and aborts
    (with an informative error) if validation fails, rather than
    persisting a malformed snapshot. Also embeds staleness-tracking
    metadata (``generated_at`` in UTC, short ``commit`` SHA) so
    reviewers can see when the baseline was last refreshed.
    """
    sorted_locations = {
        key: [[lineno, col] for lineno, col in sorted(entries)]
        for key, entries in sorted(locations.items())
    }
    canonical_tuples = {
        key: [(lineno, col) for lineno, col in entries]
        for key, entries in locations.items()
    }
    errors = _validate_baseline_payload(canonical_tuples)
    if errors:
        for err in errors:
            print(f"baseline-validation-error: {err}", file=sys.stderr)
        msg = "baseline payload failed validation; refusing to write"
        raise RuntimeError(msg)
    payload = {
        "description": (
            "SEC-1 baseline: list of `logger.exception(..., error=str(exc))`"
            " site locations per file as [lineno, col_offset] pairs."
            " Generated by"
            " scripts/check_logger_exception_str_exc.py --refresh-baseline."
            " Do not hand-edit."
        ),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": _current_commit_sha(),
        "remediation_issue": "https://github.com/Aureliolo/synthorg/issues/1488",
        "locations": sorted_locations,
    }
    with _BASELINE_PATH.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable baseline keys."""
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


_FORCE_REFRESH_FLAG = "--force"
"""Sentinel CLI flag that authorises ``--refresh-baseline`` to add sites."""


def cmd_refresh_baseline(*, force: bool = False) -> int:
    """Recompute the baseline from current source state.

    The baseline is only allowed to *shrink* (sites converted to the
    safe ``logger.warning`` pattern).  A refresh that would add new
    locations not already in the baseline fails loudly unless the
    caller explicitly passes ``--force`` (for the initial capture,
    tree-wide audits, or an explicit SEC review).  This prevents the
    accidental "add a new unsafe site, run --refresh-baseline, commit"
    workflow that would otherwise defeat the gate.
    """
    previous = _load_baseline()
    locations: dict[str, list[tuple[int, int]]] = {}
    inspection_errors: list[str] = []
    for src_path in _iter_source_files():
        try:
            hits = _scan_file(src_path)
        except InspectionError as exc:
            inspection_errors.append(str(exc))
            continue
        if hits:
            locations[_rel(src_path)] = list(hits)
    if inspection_errors:
        for line in inspection_errors:
            print(f"inspection-error: {line}", file=sys.stderr)
        print(
            "\nSEC-1: baseline refresh aborted -- some files could not be "
            "inspected. Resolve the errors above and re-run.",
            file=sys.stderr,
        )
        return 1
    additions = _baseline_additions(previous, locations)
    if additions and not force:
        for line in additions:
            print(line, file=sys.stderr)
        print(
            "\nSEC-1: refreshing the baseline would introduce the sites above, "
            "which the gate is designed to block. Convert them to the safe "
            "`logger.warning(...)` pattern first, or re-run with "
            f"`{_FORCE_REFRESH_FLAG}` if this addition has been explicitly "
            "reviewed.",
            file=sys.stderr,
        )
        return 1
    _save_baseline(locations)
    total = sum(len(v) for v in locations.values())
    print(
        f"Baseline refreshed: {total} sites across {len(locations)} files "
        f"-> {_BASELINE_PATH.relative_to(_REPO_ROOT).as_posix()}",
    )
    return 0


def _baseline_additions(
    previous: dict[str, set[tuple[int, int]]],
    current: dict[str, list[tuple[int, int]]],
) -> list[str]:
    """Return printable lines for every new location not in *previous*."""
    lines: list[str] = []
    for key, entries in sorted(current.items()):
        allowed = previous.get(key, set())
        for lineno, col in entries:
            if (lineno, col) not in allowed:
                lines.append(
                    f"refresh-would-add: {key}:{lineno}:{col}: "
                    "logger.exception(..., error=str(exc))"
                )
    return lines


def _scan_and_compare(
    src_path: Path, baseline: dict[str, set[tuple[int, int]]]
) -> list[str]:
    """Return violation lines for *src_path* versus its baseline entry."""
    try:
        hits = _scan_file(src_path)
    except InspectionError as exc:
        return [f"{_rel(src_path)}: inspection failed: {exc}"]
    if not hits:
        return []
    key = _rel(src_path)
    allowed = baseline.get(key, set())
    return [
        f"{key}:{lineno}:{col}: new logger.exception(..., error=str(exc)) site"
        for lineno, col in hits
        if (lineno, col) not in allowed
    ]


def cmd_scan_all() -> int:
    """Scan the whole src tree and compare to the baseline."""
    baseline = _load_baseline()
    violations: list[str] = []
    for src_path in _iter_source_files():
        violations.extend(_scan_and_compare(src_path, baseline))
    return _report(violations)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the given files (pre-commit entry point)."""
    baseline = _load_baseline()
    violations: list[str] = []
    for p in paths:
        path = Path(p).resolve()
        if not path.is_relative_to(_SRC_ROOT):
            continue
        if not path.exists() or path.suffix != ".py":
            continue
        violations.extend(_scan_and_compare(path, baseline))
    return _report(violations)


def _report(violations: list[str]) -> int:
    """Print violations and return a pre-commit-friendly exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(
        "\nSEC-1: `logger.exception(..., error=str(exc))` leaks credential"
        " material via traceback frame-locals AND str(exc) embedding."
        "\nReplace with:"
        "\n    logger.warning("
        "\n        EVENT_NAME,"
        "\n        ...,"
        "\n        error_type=type(exc).__name__,"
        "\n        error=safe_error_description(exc),"
        "\n    )"
        "\n"
        "\nAdd: from synthorg.observability.redaction import safe_error_description"
        "\n"
        "\nAfter converting one or more grandfathered sites, refresh the baseline:"
        "\n    python scripts/check_logger_exception_str_exc.py --refresh-baseline",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Gate on logger.exception(..., error=str(exc)) sites.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan the full src tree (CI mode).",
    )
    parser.add_argument(
        "--refresh-baseline",
        action="store_true",
        help="Rewrite _logger_exception_baseline.json from current state.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow --refresh-baseline to add new locations. Required on "
            "the initial capture or any explicitly-reviewed expansion; "
            "without this flag, refresh only permits baseline shrinkage."
        ),
    )
    args = parser.parse_args(argv)

    if args.refresh_baseline:
        return cmd_refresh_baseline(force=args.force)
    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())
