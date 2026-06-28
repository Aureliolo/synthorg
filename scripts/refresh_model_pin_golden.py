"""Regenerate the committed pin-validation golden fingerprint artifact.

Runs the pin-validation benchmark's canonical probe for every prompt
class through the deterministic provider and records one fingerprint per
class into the packaged ``pin_golden.json``. Run this deliberately
whenever a pin legitimately changes (a tier reassignment, a sampling
default, or the probe pipeline): the benchmark fails until the golden is
refreshed, which is the explicit "I changed the pin" acknowledgement that
keeps the check a genuine regression gate rather than a tautology.

Usage:
    uv run python scripts/refresh_model_pin_golden.py
"""

import asyncio
import json

from synthorg.hr.evaluation.pin_fingerprint import (
    GOLDEN_PATH,
    golden_diff,
    load_pin_golden,
)
from synthorg.hr.evaluation.pin_probe import fingerprint_for, pin_from_case_metadata
from synthorg.hr.evaluation.pin_probe_runner import PinProbeRunner
from synthorg.hr.evaluation.pin_validation_benchmark import ModelPinValidationBenchmark
from synthorg.providers.drivers.scripted import ScriptedDriver


async def _compute_golden() -> dict[str, str]:
    """Compute the fingerprint for every prompt class via the live path.

    Returns:
        A sorted map of ``prompt_class_id`` to fingerprint.
    """
    benchmark = ModelPinValidationBenchmark(golden={}, ledger=None)
    runner = PinProbeRunner(
        provider=ScriptedDriver(provider_name="pin-validation-probe"),
    )
    golden: dict[str, str] = {}
    async for case in benchmark.load_test_cases():
        output = await runner.run_case(case)
        pin = pin_from_case_metadata(case.metadata)
        golden[str(case.id)] = fingerprint_for(pin, output)
    return dict(sorted(golden.items()))


def main() -> None:
    """Compute and write the golden fingerprint artifact."""
    live = asyncio.run(_compute_golden())
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
