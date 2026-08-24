#!/usr/bin/env python3
"""CI gate: the pin golden must match the live model pins.

This is the prompt-drift regression gate and the capability-change canary in
one exact check. It recomputes every prompt class's pin fingerprint from
the current pins (``llm/model_pins.py`` + ``llm/model_capability_policy.py``)
through the deterministic scripted probe and compares the result to the
committed ``llm/pin_validation/golden.json``. Any divergence is drift:

* a pin's capability / sampling parameters changed but the golden was not
  regenerated (the canary the locked design calls for), or
* a class was added or removed from the registry without a golden refresh.

Because it recomputes rather than diffing file paths, it catches drift no
matter how the pin changed (a direct edit, a capability reassignment, or a
config default that feeds the pin), which a git-path heuristic would miss.

Exit codes
----------
* ``0`` -- clean (the committed golden equals the live fingerprints).
* ``1`` -- drift: the golden is stale; run
  ``scripts/refresh_model_pin_golden.py`` and commit the result.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from synthorg.llm.pin_validation import (
    compute_live_golden,
    golden_diff,
    load_pin_golden,
)

_REGEN_HINT = (
    "run `uv run python scripts/refresh_model_pin_golden.py` and commit the result"
)


def check(golden_path: Path | None = None) -> int:
    """Compare the live pin fingerprints against the committed golden.

    Args:
        golden_path: Override for the golden-artifact path (tests).

    Returns:
        ``0`` when the golden is fresh, ``1`` when it is stale.
    """
    try:
        live = asyncio.run(compute_live_golden())
    except Exception as exc:
        # CI gate: any failure to compute means we cannot produce a verdict;
        # surface a human-readable message rather than a raw traceback.
        print(
            f"error: failed to compute live pin fingerprints: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1
    try:
        committed = load_pin_golden(golden_path)
    except ValueError as exc:
        print(f"error: the committed pin golden is malformed: {exc}")
        print(f"  {_REGEN_HINT}")
        return 1

    drifted = golden_diff(live, committed)
    stale = tuple(sorted(set(committed) - set(live)))

    if not drifted and not stale:
        print(f"the pin golden is fresh ({len(live)} prompt classes).")
        return 0

    print("error: the pin golden is stale relative to the live model pins.")
    if drifted:
        print(f"  changed or missing from golden ({len(drifted)}):")
        for class_id in drifted:
            print(f"    - {class_id}")
    if stale:
        print(f"  in golden but no longer a registered purpose ({len(stale)}):")
        for class_id in stale:
            print(f"    - {class_id}")
    print(f"  {_REGEN_HINT}")
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    return check()


if __name__ == "__main__":
    sys.exit(main())
