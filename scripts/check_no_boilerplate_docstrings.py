#!/usr/bin/env python3
"""Gate: forbid machine-generated filler docstrings in src/synthorg/.

Blocks a fixed set of templated, tautological, or
implementation-contradicting docstring phrases (e.g. an
``"Internal helper:"`` stub, a ``Raises:`` clause asserting the input
"violates a validator", a ``Returns:`` section reading "the value
captured by the summary above"). An inaccurate docstring is worse than
no docstring: it actively misleads the reader and the generated API
docs.

Each blocked phrase is a machine-artifact shape that does not occur in
legitimate hand-written docstrings, so a substring match is a precise
signal with no false positives. A docstring that genuinely needs to
describe a helper, a validator, or a return value can always do so
without these phrases; the fix is to state the real intent / contract,
per ``CLAUDE.md`` ("Comments WHY only").

Usage
-----

    python scripts/check_no_boilerplate_docstrings.py --scan-all  # full tree
    python scripts/check_no_boilerplate_docstrings.py <file>...   # specific files
"""

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SRC_ROOT: Final[Path] = _REPO_ROOT / "src" / "synthorg"

# Each phrase is a machine-generated docstring artifact that does not
# occur in legitimate hand-written docstrings, so a substring match is
# a precise signal with no false positives. Keep this list
# conservative: only add a phrase that cannot plausibly appear in a
# genuine, hand-written docstring.
_BOILERPLATE_PHRASES: Final[tuple[str, ...]] = (
    "Internal helper:",
    "violates a validator",
    "captured by the summary above",
    "As raised by the surrounding logic",
    "The numeric value captured",
)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, phrase)`` hits for *path*, sorted by line.

    Read errors are NOT swallowed: an unreadable / undecodable file in
    scope propagates and fails the gate rather than passing unscanned.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        (lineno, phrase)
        for lineno, line in enumerate(lines, start=1)
        for phrase in _BOILERPLATE_PHRASES
        if phrase in line
    ]


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable violation messages."""
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


def _iter_src_files() -> Iterable[Path]:
    """Walk ``src/synthorg/`` for ``.py`` files."""
    yield from sorted(_SRC_ROOT.rglob("*.py"))


def _scan(path: Path) -> list[str]:
    """Return violation lines for *path*."""
    rel = _rel(path)
    return [
        f"{rel}:{lineno}: machine-generated filler docstring phrase: {phrase!r}"
        for lineno, phrase in _scan_file(path)
    ]


def _report(violations: list[str]) -> int:
    """Print violations and return a pre-commit-friendly exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(
        "\nFiller docstrings: a templated / tautological / "
        "implementation-contradicting docstring is worse than none -- it "
        "misleads the reader and the generated docs."
        "\n"
        "\nReplace the flagged docstring with a concise statement of the "
        "function's real intent and contract (Returns/Raises that match the "
        'code), or remove it. See CLAUDE.md ("Comments WHY only").',
        file=sys.stderr,
    )
    return 1


def cmd_scan_all() -> int:
    """Scan every file under ``src/synthorg/`` (CI mode)."""
    violations: list[str] = []
    for path in _iter_src_files():
        violations.extend(_scan(path))
    return _report(violations)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the given files (pre-commit entry point)."""
    src_root = _SRC_ROOT.resolve()
    violations: list[str] = []
    for raw in paths:
        path = Path(raw).resolve()
        if not path.is_relative_to(src_root):
            continue
        if not path.exists() or path.suffix != ".py":
            continue
        violations.extend(_scan(path))
    return _report(violations)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Gate on machine-generated filler docstrings in src/synthorg/.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan the full src/synthorg/ tree (CI mode).",
    )
    args = parser.parse_args(argv)

    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())
