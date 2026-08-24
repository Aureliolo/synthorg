# module-kind: code
"""Pin-validation: the drift regression gate on the model-capability policy.

Every prompt class is assigned a capability rung by
:mod:`synthorg.llm.model_capability_policy`, which is what
``engine/routing_policy/capability_policy.py`` reads when it judges a candidate
for work. :func:`synthorg.llm.model_pins.pin_for` bundles that rung with the
class's shipped sampling into a :class:`ModelPinMetadata`.

This package holds the gate on that assignment: it probes each pinned class
through a deterministic offline provider, fingerprints the pin contract plus
the probe output, and diffs the result against the committed ``golden.json``.
``scripts/check_pin_golden_fresh.py`` runs it in CI and
``scripts/refresh_model_pin_golden.py`` regenerates the golden, so a rung or
sampling change cannot land without the snapshot being deliberately refreshed.
"""

from synthorg.llm.pin_validation.benchmark import (
    BENCHMARK_NAME,
    ModelPinValidationBenchmark,
)
from synthorg.llm.pin_validation.case_models import PinGrade, PinTestCase
from synthorg.llm.pin_validation.fingerprint import (
    GOLDEN_PATH,
    golden_diff,
    load_pin_golden,
    pin_fingerprint,
)
from synthorg.llm.pin_validation.golden_compute import (
    PROBE_PROVIDER_NAME,
    compute_live_golden,
)
from synthorg.llm.pin_validation.probe_runner import PinProbeRunner

__all__ = [
    "BENCHMARK_NAME",
    "GOLDEN_PATH",
    "PROBE_PROVIDER_NAME",
    "ModelPinValidationBenchmark",
    "PinGrade",
    "PinProbeRunner",
    "PinTestCase",
    "compute_live_golden",
    "golden_diff",
    "load_pin_golden",
    "pin_fingerprint",
]
