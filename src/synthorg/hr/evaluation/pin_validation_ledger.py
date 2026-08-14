# module-kind: code
"""Durable ledger of prompt-class pin validations.

:class:`ModelPinValidationLedger` is the validator's write seam: it
stamps ``validated_at`` for a prompt class by persisting a
:class:`ModelPinValidationRow` through the
:class:`ModelPinValidationRepository`, sourcing the timestamp from the
injected :class:`Clock`. The pin-validation benchmark calls it on a clean
drift grade so the persisted ``validated_at`` means "last validated",
and the audit dashboard reads the repository to surface pin freshness.
"""

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import CapabilityLevel
from synthorg.llm.model_pin_validation import ModelPinValidationRow
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.persistence.model_pin_validation_protocol import (
    ModelPinValidationRepository,
)


class ModelPinValidationLedger:
    """Persists per-prompt-class pin-validation timestamps.

    Args:
        repository: The durable pin-validation repository.
        clock: Clock seam supplying the validation timestamp.
    """

    def __init__(
        self,
        repository: ModelPinValidationRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._clock: Clock = clock or SystemClock()

    async def record(
        self,
        *,
        prompt_class_id: PromptPurposeId,
        capability: CapabilityLevel,
    ) -> None:
        """Stamp a prompt class's *successful* pin validation now.

        Success-only by construction: it always persists a passing row, so
        a failed/drift grade can never overwrite the last-known-good
        ``validated_at``. Callers stamp this only on a clean grade; failure
        outcomes stay in the benchmark result, not the durable ledger.

        Args:
            prompt_class_id: The validated prompt class.
            capability: The design capability validated against.
        """
        row = ModelPinValidationRow(
            prompt_class_id=prompt_class_id,
            validated_at=self._clock.now(),
            capability=capability,
        )
        await self._repository.save(row)


__all__ = ["ModelPinValidationLedger"]
