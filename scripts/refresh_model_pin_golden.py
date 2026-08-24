"""Regenerate the committed pin-validation golden fingerprint artifact.

Runs the pin-validation benchmark's canonical probe for every prompt
class through the deterministic provider and records one fingerprint per
class into the packaged ``llm/pin_validation/golden.json``. Run this
deliberately whenever a pin legitimately changes (a capability reassignment, a
sampling default, or the probe pipeline): the CI canary fails until the golden
is refreshed, which is the explicit "I changed the pin" acknowledgement that
keeps the check a genuine regression gate rather than a tautology.

Usage:
    uv run python scripts/refresh_model_pin_golden.py
"""

import asyncio
import json

from synthorg.llm.pin_validation import (
    GOLDEN_PATH,
    compute_live_golden,
    golden_diff,
    load_pin_golden,
)


def main() -> None:
    """Compute and write the golden fingerprint artifact."""
    live = asyncio.run(compute_live_golden())
    try:
        previous_golden = load_pin_golden()
    except ValueError:
        # A malformed committed golden is the exact corruption this
        # recovery script exists to repair; treat it as empty so every
        # live pin reports as changed and the rewrite still proceeds.
        previous_golden = {}
    changed = golden_diff(live, previous_golden)
    GOLDEN_PATH.write_text(
        json.dumps(live, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(live)} fingerprints to {GOLDEN_PATH}")
    if changed:
        print(f"Changed pins ({len(changed)}):")
        for class_id in changed:
            print(f"  - {class_id}")
    else:
        print("No pins changed.")


if __name__ == "__main__":
    main()
