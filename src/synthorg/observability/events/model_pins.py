"""Model-pin validation event constants."""

from typing import Final

# Pin-validation benchmark + validator.
MODEL_PIN_BENCHMARK_DRIFT: Final[str] = "model_pin.benchmark.drift"
# A case's id disagrees with its pinned prompt_class_id (malformed case).
MODEL_PIN_CASE_MISMATCH: Final[str] = "model_pin.benchmark.case_mismatch"

# Committed golden-fingerprint artifact load.
MODEL_PIN_GOLDEN_ABSENT: Final[str] = "model_pin.golden.absent"
MODEL_PIN_GOLDEN_MALFORMED: Final[str] = "model_pin.golden.malformed"
