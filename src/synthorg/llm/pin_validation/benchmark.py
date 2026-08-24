# module-kind: code
"""Pin-validation benchmark: grade each prompt class against its capability.

Iterates the prompt-purpose registry and, for each class, emits a test case
carrying the canonical pin (capability model id plus sampling parameters) and
the committed golden fingerprint. A :class:`PinProbeRunner` runs the canonical
probe against the pinned capability; :meth:`grade` recomputes the live
fingerprint and compares it to the golden. A mismatch is drift.

The golden is the durable record of when a pin last held: it is committed,
diffable and versioned with the code that produced it, so a clean grade needs
no separate runtime stamp to be auditable.
"""

from collections.abc import AsyncIterator, Mapping
from types import MappingProxyType
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.llm.model_pins import pin_for
from synthorg.llm.pin_validation.case_models import PinGrade, PinTestCase
from synthorg.llm.pin_validation.probe import (
    PIN_META_KEY,
    fingerprint_for,
    pin_from_case_metadata,
    pin_metadata_payload,
    probe_input_data,
)
from synthorg.llm.prompt_purpose import PROMPT_PURPOSE_REGISTRY
from synthorg.observability import get_logger
from synthorg.observability.events.model_pins import (
    MODEL_PIN_BENCHMARK_DRIFT,
    MODEL_PIN_CASE_MISMATCH,
    MODEL_PIN_GOLDEN_ABSENT,
)

logger = get_logger(__name__)

BENCHMARK_NAME: Final[str] = "model-pin-validation"
_FINGERPRINT_PREVIEW: Final[int] = 12


class ModelPinValidationBenchmark:
    """Validates each prompt class's pin against its design capability.

    Args:
        golden: Committed fingerprint map (``prompt_class_id`` to
            expected fingerprint) the live fingerprint is graded against.
    """

    def __init__(self, *, golden: dict[str, str]) -> None:
        self._golden: Mapping[str, str] = MappingProxyType(dict(golden))

    @property
    def name(self) -> str:
        """Benchmark name."""
        return BENCHMARK_NAME

    async def load_test_cases(self) -> AsyncIterator[PinTestCase]:
        """Stream one pin-validation case per registered prompt purpose.

        Yields:
            One :class:`PinTestCase` per prompt purpose.
        """
        for purpose in PROMPT_PURPOSE_REGISTRY.all_purposes():
            pid = purpose.id
            pin = pin_for(pid)
            yield PinTestCase(
                id=NotBlankStr(str(pid)),
                input_data=probe_input_data(pid),
                expected_output=self._golden.get(str(pid), ""),
                metadata={PIN_META_KEY: pin_metadata_payload(pin)},
            )

    async def grade(
        self,
        *,
        case: PinTestCase,
        agent_output: str,
    ) -> PinGrade:
        """Grade a prompt class's pin against the committed golden.

        Args:
            case: The pin-validation case.
            agent_output: The probe runner's raw provider output.

        Returns:
            A passing grade when the live fingerprint matches the golden;
            a failing grade tagged with the drift otherwise.
        """
        pin = pin_from_case_metadata(case.metadata)
        if str(pin.prompt_class_id) != str(case.id):
            # The case id and its pinned prompt_class_id must name the same
            # class; otherwise we would grade class A against class B's golden.
            # A mismatch is a malformed case, not drift.
            logger.warning(
                MODEL_PIN_CASE_MISMATCH,
                case_id=case.id,
                pin_class_id=str(pin.prompt_class_id),
            )
            return PinGrade(
                passed=False,
                score=0.0,
                explanation=(
                    f"malformed case: id {case.id} != pinned "
                    f"prompt_class_id {pin.prompt_class_id}"
                ),
            )
        live = fingerprint_for(pin, agent_output)
        expected = case.expected_output
        if not expected:
            # An empty expected fingerprint means the class is absent from
            # the committed golden (a fresh checkout, a forgotten regen, or
            # a newly-added purpose), not a genuine capability change.
            # Surface it distinctly so an operator runs the regen rather
            # than hunting a non-existent drift.
            logger.warning(MODEL_PIN_GOLDEN_ABSENT, prompt_class_id=case.id)
            return PinGrade(
                passed=False,
                score=0.0,
                explanation=(
                    "absent from golden; run scripts/refresh_model_pin_golden.py"
                ),
            )
        if live != expected:
            logger.warning(
                MODEL_PIN_BENCHMARK_DRIFT,
                prompt_class_id=case.id,
                expected_fingerprint=expected[:_FINGERPRINT_PREVIEW],
                live_fingerprint=live[:_FINGERPRINT_PREVIEW],
            )
            return PinGrade(
                passed=False,
                score=0.0,
                explanation=(
                    f"pin drift: live {live[:_FINGERPRINT_PREVIEW]} "
                    f"!= golden {expected[:_FINGERPRINT_PREVIEW]}"
                ),
            )
        return PinGrade(
            passed=True,
            score=1.0,
            explanation="pin validated: fingerprint matches golden",
        )


__all__ = ["BENCHMARK_NAME", "ModelPinValidationBenchmark"]
