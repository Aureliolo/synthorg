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
* 2: ruff could not be invoked, or refused the config (setup failure, not a
  regression)
"""

import subprocess
import sys

_DOC_RULES = "DOC201,DOC202,DOC501"
_DEFAULT_PATHS = ("src", "tests")
# ruff's own contract: 0 clean, 1 violations, anything else an error it could
# not get past. Mapping every non-zero to "violations" reported a rule
# selector the pinned ruff knows but a stale PATH ruff does not as a
# docstring finding, which is a verdict the scan never reached.
_RUFF_CLEAN = 0
_RUFF_VIOLATIONS = 1


def main(argv: list[str] | None = None) -> int:
    """Run the DOC rules over *argv* (or the default paths) via ruff.

    Args:
        argv: Paths to check. Empty / ``None`` falls back to the
            ``src`` and ``tests`` trees so pre-push can run it with
            ``pass_filenames: false``.

    Returns:
        ``0`` when ruff reports no DOC violations, ``1`` when it does,
        and ``2`` when ruff itself cannot be invoked or refuses the config.
    """
    paths = list(argv) if argv else list(_DEFAULT_PATHS)
    try:
        result = subprocess.run(
            # Through this interpreter, not a bare `ruff`: a PATH lookup finds
            # whatever ruff the machine happens to carry, which is a different
            # engine from the pinned one every sibling gate runs under.
            [sys.executable, "-m", "ruff", "check", f"--select={_DOC_RULES}", *paths],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(
            f"Could not invoke ruff for docstring-completeness gate: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    # Re-emit ruff's report through Python-level stdout/stderr (not the raw file
    # descriptors a bare subprocess would inherit) so the consolidated pre-push
    # runner -- which captures each gate's Python-level output to attribute
    # findings to it under the parallel pool -- surfaces the violations instead
    # of losing them.
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode in {_RUFF_CLEAN, _RUFF_VIOLATIONS}:
        return result.returncode
    print(
        f"ruff exited {result.returncode} without checking the DOC rules; "
        f"the scan reached no verdict.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
