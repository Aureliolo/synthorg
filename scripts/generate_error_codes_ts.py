#!/usr/bin/env python3
"""Generate ``web/src/api/types/error-codes.gen.ts``.

Reads ``synthorg.core.error_taxonomy`` and emits a TypeScript mirror.

The web dashboard previously hand-mirrored a tiny subset of the
backend ``ErrorCode`` enum and inlined raw numeric literals everywhere
else (e.g. ``error_code: 3000``). This generator emits a TypeScript
module that stays in lockstep with the Python source of truth so the
frontend can discriminate on named constants instead of magic numbers.

Output module shape (every member -- never a partial mirror):

    // AUTO-GENERATED: do not edit by hand.
    // Regenerate with: uv run python scripts/generate_error_codes_ts.py
    // Source: src/synthorg/core/error_taxonomy.py

    export const ErrorCode = {
        UNAUTHORIZED: 1000,
        ...
    } as const;
    export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];

    export const ErrorCategory = {
        AUTH: "auth",
        ...
    } as const;
    export type ErrorCategory = (typeof ErrorCategory)[keyof typeof ErrorCategory];

The accompanying gate at
``scripts/check_error_codes_ts_in_sync.py`` re-runs the generator into
a temp file and fails if the committed output drifts.

Usage::

    uv run python scripts/generate_error_codes_ts.py
    uv run python scripts/generate_error_codes_ts.py --check  # exit 1 on drift
    uv run python scripts/generate_error_codes_ts.py --stdout
"""

import argparse
import sys
from pathlib import Path
from typing import Final

# ``synthorg.core.error_taxonomy`` is a pure leaf -- importing it
# pulls only stdlib enums + url helpers, no API / persistence layers.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

_OUTPUT_REL: Final[str] = "web/src/api/types/error-codes.gen.ts"

_HEADER: Final[str] = (
    "// AUTO-GENERATED: do not edit by hand.\n"
    "// Regenerate with: uv run python scripts/generate_error_codes_ts.py\n"
    "// Drift check (pre-push): "
    "uv run python scripts/check_error_codes_ts_in_sync.py\n"
    "// Source: src/synthorg/core/error_taxonomy.py\n"
    "// Contract: web/CLAUDE.md -> 'Error-code constants (MANDATORY)'\n"
)


def _render() -> str:
    """Return the full TypeScript module source (LF line endings)."""
    code_entries = "\n".join(
        f"    {member.name}: {member.value}," for member in ErrorCode
    )
    category_entries = "\n".join(
        f'    {member.name}: "{member.value}",' for member in ErrorCategory
    )
    return (
        f"{_HEADER}\n"
        "export const ErrorCode = {\n"
        f"{code_entries}\n"
        "} as const;\n"
        "export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];\n"
        "\n"
        "export const ErrorCategory = {\n"
        f"{category_entries}\n"
        "} as const;\n"
        "export type ErrorCategory ="
        " (typeof ErrorCategory)[keyof typeof ErrorCategory];\n"
    )


def main() -> int:
    """Write the generated file (or compare against it)."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Compare against the committed file; exit 1 on drift.",
    )
    group.add_argument(
        "--stdout",
        action="store_true",
        help="Print the generated module to stdout instead of writing it.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    output_path = repo_root / _OUTPUT_REL
    rendered = _render()

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        if not output_path.exists():
            print(
                f"missing generated file: {output_path}",
                file=sys.stderr,
            )
            return 1
        existing = output_path.read_text(encoding="utf-8")
        if existing != rendered:
            print(
                f"\n{output_path.relative_to(repo_root).as_posix()} is "
                f"out of sync with src/synthorg/core/error_taxonomy.py.\n"
                f"\nRun: uv run python scripts/generate_error_codes_ts.py",
                file=sys.stderr,
            )
            return 1
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Use newline="\n" so Windows checkouts don't drift the gate by
    # writing CRLF line endings into a file the gate compares
    # byte-for-byte.
    with output_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered)
    print(
        f"wrote {len(ErrorCode)} ErrorCode + {len(ErrorCategory)} "
        f"ErrorCategory entries to "
        f"{output_path.relative_to(repo_root).as_posix()}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
