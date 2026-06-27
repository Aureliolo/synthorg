"""Model-pin validation event constants."""

from typing import Final

# Persistence repository (model_pin_validations table).
MODEL_PIN_VALIDATION_FETCHED: Final[str] = "model_pin.validation.fetched"
MODEL_PIN_VALIDATION_LISTED: Final[str] = "model_pin.validation.listed"
MODEL_PIN_VALIDATION_FAILED: Final[str] = "model_pin.validation.failed"

# Pin-validation benchmark + validator.
MODEL_PIN_BENCHMARK_DRIFT: Final[str] = "model_pin.benchmark.drift"
MODEL_PIN_VALIDATION_STAMPED: Final[str] = "model_pin.validation.stamped"
# The drift grade was clean but persisting the validated-at timestamp
# failed; the verdict is unaffected, so this is logged (WARNING) rather
# than flipping a clean grade to failed.
MODEL_PIN_VALIDATION_STAMP_FAILED: Final[str] = "model_pin.validation.stamp_failed"

# Committed golden-fingerprint artifact load.
MODEL_PIN_GOLDEN_ABSENT: Final[str] = "model_pin.golden.absent"
MODEL_PIN_GOLDEN_MALFORMED: Final[str] = "model_pin.golden.malformed"
