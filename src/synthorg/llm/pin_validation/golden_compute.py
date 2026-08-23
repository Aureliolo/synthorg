# module-kind: code
"""Compute the live pin-validation golden from the current pins.

Both the deliberate regeneration tool
(``scripts/refresh_model_pin_golden.py``) and the CI freshness canary
(``scripts/check_pin_golden_fresh.py``) need the same answer: the
fingerprint every prompt class produces *right now*, through the
deterministic scripted provider. Sharing one routine keeps the regen
artifact and the gate that guards it bit-identical, so the canary can
never disagree with what the regen tool would write.
"""

from typing import Final

from synthorg.llm.pin_validation.benchmark import ModelPinValidationBenchmark
from synthorg.llm.pin_validation.probe import fingerprint_for, pin_from_case_metadata
from synthorg.llm.pin_validation.probe_runner import PinProbeRunner
from synthorg.providers.drivers.scripted import ScriptedDriver

PROBE_PROVIDER_NAME: Final[str] = "pin-validation-probe"


async def compute_live_golden() -> dict[str, str]:
    """Compute the current fingerprint for every prompt class.

    Iterates the pin-validation benchmark's canonical probe for each
    registered prompt class through the deterministic scripted provider,
    so the result depends only on the live pins and the probe pipeline,
    never on wall-clock time or network state.

    Returns:
        A map of ``prompt_class_id`` to fingerprint, sorted by id.

    Raises:
        ValueError: If a test case's metadata pins a prompt class that
            differs from the case id (a malformed case).
    """
    benchmark = ModelPinValidationBenchmark(golden={})
    runner = PinProbeRunner(provider=ScriptedDriver(provider_name=PROBE_PROVIDER_NAME))
    golden: dict[str, str] = {}
    async for case in benchmark.load_test_cases():
        output = await runner.run_case(case)
        pin = pin_from_case_metadata(case.metadata)
        # Mirror ``ModelPinValidationBenchmark.grade``: a case whose metadata
        # pins a different prompt class than its id is malformed; fail loudly
        # rather than write a wrong golden the freshness canary would trust.
        if str(pin.prompt_class_id) != str(case.id):
            msg = (
                "malformed pin-validation case: "
                f"id {case.id} != pinned prompt_class_id {pin.prompt_class_id}"
            )
            raise ValueError(msg)
        golden[str(case.id)] = fingerprint_for(pin, output)
    return dict(sorted(golden.items()))


__all__ = ["PROBE_PROVIDER_NAME", "compute_live_golden"]
