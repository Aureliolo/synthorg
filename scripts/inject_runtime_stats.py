#!/usr/bin/env python3
"""Substitute build-time numeric values into public docs.

Reads ``data/runtime_stats.yaml`` and rewrites the inner content of
every ``<!--RS:NAME-->...<!--/RS-->`` marker in the in-scope docs:

* ``README.md``
* ``docs/index.md``
* ``docs/roadmap/index.md``
* ``docs/architecture/decisions.md``

The rewrite is idempotent: running twice produces identical output.
Unknown marker names raise :class:`_UnknownStatError` so typos in
markers fail loudly rather than silently leaving stale text.

A scoped file that does not exist on disk is an error: the script logs
the missing path to stderr, continues processing the remaining files so
every problem surfaces in one run, then exits non-zero. The
docs-freshness contract requires injection coverage on every entry in
``_SCOPED_FILES``; silently skipping would let the build publish stale
markers in any doc that vanished from the checkout.

Run after ``scripts/generate_runtime_stats.py`` and before
``zensical build``::

    uv run python scripts/generate_runtime_stats.py
    uv run python scripts/inject_runtime_stats.py
"""

import re
import sys
from pathlib import Path
from typing import Any, Final

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_STATS_FILE: Path = REPO_ROOT / "data" / "runtime_stats.yaml"

_SCOPED_FILES: Final[tuple[str, ...]] = (
    "README.md",
    "docs/index.md",
    "docs/roadmap/index.md",
    "docs/architecture/decisions.md",
)

_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--RS:([a-z0-9_]+)-->(.*?)<!--/RS-->", re.DOTALL
)


class _UnknownStatError(Exception):
    """A marker references a stat name not present in runtime_stats.yaml.

    Carries the offending marker name and a discriminator for the two
    failure modes so callers (and logs) can route by the specific
    bug rather than parsing the message string.
    """

    NOT_FOUND: Final[str] = "not_found"
    MISSING_DISPLAY: Final[str] = "missing_display"

    def __init__(self, marker_name: str, issue: str, message: str) -> None:
        self.marker_name = marker_name
        self.issue = issue
        super().__init__(message)


def _lookup_display(stats: dict[str, Any], name: str) -> str:
    """Return ``stats[name]['display']`` or raise _UnknownStatError."""
    entry = stats.get(name)
    if not isinstance(entry, dict):
        msg = (
            f"<!--RS:{name}--> references unknown stat name; add it to "
            "data/runtime_stats.yaml or fix the marker"
        )
        raise _UnknownStatError(name, _UnknownStatError.NOT_FOUND, msg)
    display = entry.get("display")
    if not isinstance(display, str) or not display.strip():
        msg = (
            f"<!--RS:{name}--> resolved entry is missing a non-empty 'display' "
            "field; regenerate data/runtime_stats.yaml"
        )
        raise _UnknownStatError(name, _UnknownStatError.MISSING_DISPLAY, msg)
    return display


def rewrite_text(text: str, stats: dict[str, Any]) -> str:
    """Return *text* with every marker's inner content replaced by display.

    *stats* is the ``stats`` block from ``data/runtime_stats.yaml``,
    keyed by snake-case stat name.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"<!--RS:{name}-->{_lookup_display(stats, name)}<!--/RS-->"

    return _MARKER_RE.sub(_sub, text)


def inject_file(path: Path, stats: dict[str, Any]) -> bool:
    """Rewrite *path* in-place; return True iff the file changed.

    Missing files are not an error: the gate, not the injector, owns
    file inventory enforcement. The caller is responsible for warning
    when a scoped path does not exist on disk.
    """
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    rewritten = rewrite_text(original, stats)
    if rewritten == original:
        return False
    path.write_text(rewritten, encoding="utf-8")
    return True


def _load_stats() -> dict[str, Any]:
    """Load `data/runtime_stats.yaml` and return its ``stats`` block."""
    if not _STATS_FILE.is_file():
        msg = (
            f"runtime_stats.yaml not found at {_STATS_FILE}; "
            "run scripts/generate_runtime_stats.py first"
        )
        raise FileNotFoundError(msg)
    try:
        loaded = yaml.safe_load(_STATS_FILE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"runtime_stats.yaml at {_STATS_FILE} is not valid YAML: {exc}"
        raise TypeError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"runtime_stats.yaml at {_STATS_FILE} is not a mapping"
        raise TypeError(msg)
    stats = loaded.get("stats")
    if not isinstance(stats, dict):
        msg = "runtime_stats.yaml is missing 'stats' block"
        raise TypeError(msg)
    return stats


def main() -> int:
    """Inject every marker in `_SCOPED_FILES`; return shell exit code."""
    try:
        stats = _load_stats()
    except (FileNotFoundError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    changed_count = 0
    missing_count = 0
    for rel in _SCOPED_FILES:
        abs_path = REPO_ROOT / rel
        if not abs_path.is_file():
            print(
                f"error: scoped file {rel} not found on disk; "
                "update _SCOPED_FILES or restore the file",
                file=sys.stderr,
            )
            missing_count += 1
            continue
        try:
            changed = inject_file(abs_path, stats)
        except _UnknownStatError as exc:
            print(
                f"error: {rel}: marker={exc.marker_name!r} "
                f"issue={exc.issue} message={exc!s}",
                file=sys.stderr,
            )
            return 1
        verb = "rewrote" if changed else "checked"
        print(f"{verb}: {rel}")
        if changed:
            changed_count += 1
    unchanged = len(_SCOPED_FILES) - changed_count - missing_count
    print(
        f"done: {changed_count} file(s) rewritten, "
        f"{unchanged} unchanged, {missing_count} missing"
    )
    if missing_count:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
