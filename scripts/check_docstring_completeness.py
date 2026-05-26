#!/usr/bin/env python3
"""Pre-push / CI gate: docstring completeness (DOC201 / DOC202 / DOC501).

Thin wrapper that shells out to ruff's pydoclint rules so the
docstring-completeness convention ships an explicit, greppable
enforcement gate (per the Convention Rollout rule) rather than living
only inside the shared ``ruff check`` invocation. Ruff stays the engine:
this gate runs exactly the three DOC rules and inherits the
per-file-ignores scope (swept packages only) declared in
``pyproject.toml``, so it can never drift from the config that the
standard ``ruff check`` already enforces.

Exit codes:

* 0: no docstring-completeness violations in scope
* 1: ruff reported one or more DOC201 / DOC202 / DOC501 violations
* 2: ruff could not be invoked (setup failure, not a regression)
"""

import subprocess
import sys

_DOC_RULES = "DOC201,DOC202,DOC501"
_DEFAULT_PATHS = ("src", "tests")


def main(argv: list[str] | None = None) -> int:
    """Run the DOC rules over *argv* (or the default paths) via ruff.

    Args:
        argv: Paths to check. Empty / ``None`` falls back to the
            ``src`` and ``tests`` trees so pre-push can run it with
            ``pass_filenames: false``.

    Returns:
        ``0`` when ruff reports no DOC violations, ``1`` when it does,
        and ``2`` when ruff itself cannot be invoked.
    """
    paths = list(argv) if argv else list(_DEFAULT_PATHS)
    try:
        result = subprocess.run(
            ["ruff", "check", f"--select={_DOC_RULES}", *paths],
            check=False,
        )
    except OSError as exc:
        print(
            f"Could not invoke ruff for docstring-completeness gate: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    return 1 if result.returncode != 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
