#!/usr/bin/env python3
"""Pre-commit / CI gate: ban RUF100 self-cloaking noqa directives.

``RUF100`` (``unused-noqa``) is ruff's own check for dead suppression
directives. Listing ``RUF100`` *alongside another code* in a single
``noqa`` directive makes that directive suppress RUF100's complaint
about its own sibling code, so a dead directive can hide in plain sight
even on a fully linted path: a dead type-checking suppression cloaked by
a trailing RUF100 token survives every lint pass.

The fix is structural: RUF100 must never share a ``noqa`` list with
another code. A genuinely-needed suppression carries only the codes it
suppresses; if one of those goes dead, RUF100 is then free to report it.

This gate scans every tracked ``*.py`` file across the whole tree
(``src/``, ``tests/``, ``evals/``, ``scripts/``, ``docker/``, root) and
fails when ``RUF100`` appears in a ``noqa`` code list of length >= 2.

Usage::

    uv run python scripts/check_no_ruff100_self_cloak.py
"""

import argparse
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Final

# Capture the comma-separated run of ruff codes in a suppression
# directive. A code is an uppercase prefix followed by digits (e.g.
# ``TC001``, ``RUF100``); the run stops at the first non-code token
# (such as a trailing `` -- reason``).
_NOQA_CODES: Final[re.Pattern[str]] = re.compile(
    r"#\s*noqa\s*:\s*(?P<codes>[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)"
)
_TARGET_CODE: Final[str] = "RUF100"


def _tracked_python_files(root: Path) -> list[Path]:
    """Return tracked ``*.py`` paths under *root* via ``git ls-files``."""
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [root / line for line in result.stdout.splitlines() if line]


def _is_self_cloak(snippet: str) -> bool:
    """Return whether *snippet* carries an RUF100 self-cloak directive.

    A self-cloak is a ``noqa`` listing ``RUF100`` alongside at least one
    other code, so RUF100 suppresses its own complaint about a sibling
    that may be dead.
    """
    match = _NOQA_CODES.search(snippet)
    if match is None:
        return False
    codes = {code.strip() for code in match.group("codes").split(",")}
    return _TARGET_CODE in codes and len(codes) > 1


def _self_cloak_lines(path: Path) -> list[int]:
    """Return 1-based line numbers carrying an RUF100 self-cloak.

    Scans only comment tokens (via :mod:`tokenize`) so a ``noqa`` pattern
    living inside a docstring or string literal -- which ruff never reads
    as a directive -- cannot raise a false positive. Falls back to a raw
    line scan when the file does not tokenise (e.g. a deliberately
    malformed fixture); the fallback can only over-report on contrived
    string literals, never miss a real comment-borne directive.
    """
    try:
        with path.open("rb") as handle:
            return [
                token.start[0]
                for token in tokenize.tokenize(handle.readline)
                if token.type == tokenize.COMMENT and _is_self_cloak(token.string)
            ]
    except tokenize.TokenError, SyntaxError:
        text = path.read_text(encoding="utf-8")
        return [
            lineno
            for lineno, line in enumerate(text.splitlines(), start=1)
            if _is_self_cloak(line)
        ]


def _scan(root: Path) -> int:
    try:
        files = _tracked_python_files(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"could not enumerate tracked files under {root}: {exc}", file=sys.stderr)
        return 2

    violations = 0
    for path in sorted(files):
        rel = path.relative_to(root).as_posix()
        try:
            lines = _self_cloak_lines(path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"{rel}: could not read file in RUF100 scan: {exc}", file=sys.stderr)
            return 2
        for lineno in lines:
            print(
                (
                    f"{rel}:{lineno}: RUF100 must not share a noqa list with "
                    "another code (self-cloak hides a dead directive). Drop "
                    "RUF100; keep only the codes you actually suppress."
                ),
                file=sys.stderr,
            )
            violations += 1

    return 1 if violations else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Path to the repo root (defaults to cwd).",
    )
    args = parser.parse_args(argv)
    return _scan(args.repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
