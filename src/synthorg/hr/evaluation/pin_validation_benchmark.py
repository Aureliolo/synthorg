# module-kind: code
"""Pin-validation benchmark: grade each prompt class against its tier.

:class:`ModelPinValidationBenchmark` is the concrete
:class:`ExternalBenchmark` that gives ``ModelPinMetadata`` a real
consumer. It iterates the prompt-purpose registry and, for each class,
emits a test case carrying the canonical pin (tier model id plus sampling
parameters) and the committed golden fingerprint. The registry's injected
:class:`PinProbeRunner` runs the canonical probe against the pinned tier
through a real ``provider.complete`` call; :meth:`grade` recomputes the
live fingerprint and compares it to the golden. A mismatch is drift.

On a clean grade the benchmark stamps ``validated_at`` for the class
through the :class:`ModelPinValidationLedger`, so a passing grade is the
eval refresh that records when the class was last validated against its
tier. The stamp is best-effort: a persistence failure is logged but never
flips the drift verdict, which depends only on the fingerprint comparison.
"""

from collections.abc import AsyncIterator, Mapping
from types import MappingProxyType
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    EvalTestCase,
)
from synthorg.hr.evaluation.pin_probe import (
    PIN_META_KEY,
    fingerprint_for,
    pin_from_case_metadata,
    pin_metadata_payload,
    probe_input_data,
)
from synthorg.hr.evaluation.pin_validation_ledger import ModelPinValidationLedger
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_capability_policy import capability_for_purpose
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PROMPT_PURPOSE_REGISTRY
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.model_pins import (
    MODEL_PIN_BENCHMARK_DRIFT,
    MODEL_PIN_CASE_MISMATCH,
    MODEL_PIN_GOLDEN_ABSENT,
    MODEL_PIN_VALIDATION_STAMP_FAILED,
    MODEL_PIN_VALIDATION_STAMPED,
)

logger = get_logger(__name__)

_BENCHMARK_NAME: Final[str] = "model-pin-validation"
_SOURCE_URL: Final[str] = (
    "https://github.com/Aureliolo/synthorg/blob/main/docs/reference/model-capability-policy.md"
)
_LICENSE: Final[str] = "BUSL-1.1"
_CASE_TAGS: Final[tuple[BehaviorTag, ...]] = (BehaviorTag.VERIFICATION,)
_FINGERPRINT_PREVIEW: Final[int] = 12


class ModelPinValidationBenchmark:
    """Validates each prompt class's pin against its design tier.

    Args:
        golden: Committed fingerprint map (``prompt_class_id`` to
            expected fingerprint) the live fingerprint is graded against.
        ledger: Durable validator that stamps ``validated_at`` on a clean
            grade; ``None`` when no persistence backend is wired (the
            drift check still runs, the stamp is skipped).
    """

    def __init__(
        self,
        *,
        golden: dict[str, str],
        ledger: ModelPinValidationLedger | None = None,
    ) -> None:
        self._golden: Mapping[str, str] = MappingProxyType(dict(golden))
        self._ledger = ledger

    @property
    def name(self) -> str:
        """Benchmark name."""
        return _BENCHMARK_NAME

    @property
    def source_url(self) -> str:
        """URL to the tier-policy documentation."""
        return _SOURCE_URL

    @property
    def license(self) -> str:
        """License identifier."""
        return _LICENSE

    async def load_test_cases(
        self,
        *,
        behavior_tags: frozenset[BehaviorTag] | None = None,
    ) -> AsyncIterator[EvalTestCase]:
        """Stream one pin-validation case per registered prompt purpose.

        Args:
            behavior_tags: Filter to these tags. Every case is tagged
                ``VERIFICATION``; a filter excluding it yields nothing.

        Yields:
            One :class:`EvalTestCase` per prompt purpose.
        """
        if behavior_tags is not None and BehaviorTag.VERIFICATION not in behavior_tags:
            return
        for purpose in PROMPT_PURPOSE_REGISTRY.all_purposes():
            pid = purpose.id
            pin = pin_for(pid)
            yield EvalTestCase(
                id=NotBlankStr(str(pid)),
                behavior_tags=_CASE_TAGS,
                input_data=probe_input_data(pid),
                expected_output=self._golden.get(str(pid), ""),
                metadata={PIN_META_KEY: pin_metadata_payload(pin)},
            )

    async def grade(
        self,
        *,
        case: EvalTestCase,
        agent_output: str,
    ) -> BenchmarkGrade:
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
            # class; otherwise we would grade class A against class B's golden
            # and could stamp the wrong entity as validated. A mismatch is a
            # malformed case, not drift, so fail without touching the ledger.
            logger.warning(
                MODEL_PIN_CASE_MISMATCH,
                case_id=case.id,
                pin_class_id=str(pin.prompt_class_id),
            )
            return BenchmarkGrade(
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
            # a newly-added purpose), not a genuine algorithm/tier change.
            # Surface it distinctly so an operator runs the regen rather
            # than hunting a non-existent drift.
            logger.warning(MODEL_PIN_GOLDEN_ABSENT, prompt_class_id=case.id)
            return BenchmarkGrade(
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
            return BenchmarkGrade(
                passed=False,
                score=0.0,
                explanation=(
                    f"pin drift: live {live[:_FINGERPRINT_PREVIEW]} "
                    f"!= golden {expected[:_FINGERPRINT_PREVIEW]}"
                ),
            )
        await self._stamp_validation(pin)
        return BenchmarkGrade(
            passed=True,
            score=1.0,
            explanation="pin validated: fingerprint matches golden",
        )

    async def _stamp_validation(self, pin: ModelPinMetadata) -> None:
        """Stamp ``validated_at`` for the fingerprinted class (best-effort).

        Stamps ``pin.prompt_class_id`` -- the entity that was actually
        fingerprinted -- so the ledger records the validated class, not the
        case label. A persistence failure is logged but never propagated, so
        a DB hiccup cannot flip a clean drift verdict to failed; interpreter-
        critical errors propagate.

        Args:
            pin: The cleanly-graded class's pin.
        """
        if self._ledger is None:
            return
        pid = pin.prompt_class_id
        tier = capability_for_purpose(pid)
        try:
            await self._ledger.record(prompt_class_id=pid, tier=tier)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # WARNING, not ERROR: the stamp is best-effort and its failure
            # never affects the (clean) drift verdict, so it must not page.
            logger.warning(
                MODEL_PIN_VALIDATION_STAMP_FAILED,
                prompt_class_id=str(pid),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.info(MODEL_PIN_VALIDATION_STAMPED, prompt_class_id=str(pid), tier=tier)


__all__ = ["ModelPinValidationBenchmark"]
