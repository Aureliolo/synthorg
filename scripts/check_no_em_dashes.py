#!/usr/bin/env python3
"""Pre-commit hook: reject files containing em-dashes (U+2014)."""

import sys
from pathlib import Path

# Build patterns without embedding the literal HTML entity in this file
# (otherwise the pre-commit hook flags this script itself).
_PATTERNS = ("\u2014", "&" + "mdash;", "&" + "#8212;", "&" + "#x2014;")
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Auto-generated files whose em-dash content is produced by tooling
# (release-please regenerates the changelog on every release from
# historical commit subjects). Hand-edits are discouraged; excluding
# them avoids forcing manual scrubs that would be overwritten on the
# next release anyway.
_EXCLUDED_RELATIVE: frozenset[str] = frozenset({".github/CHANGELOG.md"})


def main() -> int:
    """Scan files for em-dash characters and report locations.

    Returns ``0`` clean, ``1`` when an em-dash is found, and ``2``
    (fail-closed) when a file cannot be read or decoded: a skipped file
    could hide an em-dash, so an unreadable file fails the gate rather
    than passing silently.
    """
    found = False
    read_errors: list[str] = []
    for path in sys.argv[1:]:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(_REPO_ROOT):
            continue
        relative = resolved.relative_to(_REPO_ROOT).as_posix()
        if relative in _EXCLUDED_RELATIVE:
            continue
        try:
            with resolved.open(encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if any(p in line for p in _PATTERNS):
                        print(f"{path}:{lineno}: {line.rstrip()}")
                        found = True
        except (UnicodeDecodeError, OSError) as exc:
            read_errors.append(f"{path}: {exc}")
    if read_errors:
        print("ERROR: em-dash scan could not read these files:", file=sys.stderr)
        for err in read_errors:
            print(f"  {err}", file=sys.stderr)
        return 2
    if found:
        print(
            "\nEm-dashes (U+2014) found. Replace with the ASCII punctuation that"
            " matches the sentence. Usually one of:\n"
            "  : (colon)          - introducing a list, clause, or explanation\n"
            "  ; (semicolon)      - joining two independent clauses\n"
            "  , (comma)          - short aside or enumeration\n"
            "  . (period)         - split into two sentences\n"
            "  ( ... )            - parenthetical aside\n"
            "  -                  - compound hyphen only (do NOT use for asides)\n"
            "Bare '--' is almost never right; prefer one of the above or rewrite."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
