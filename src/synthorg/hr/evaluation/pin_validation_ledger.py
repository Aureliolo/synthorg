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

from synthorg.budget.model_tier import TierName
from synthorg.core.clock import Clock, SystemClock
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
        tier: TierName,
        passed: bool,
    ) -> None:
        """Stamp a prompt class's pin validation at the current time.

        Args:
            prompt_class_id: The validated prompt class.
            tier: The design tier validated against.
            passed: Whether the drift grade passed.
        """
        row = ModelPinValidationRow(
            prompt_class_id=prompt_class_id,
            validated_at=self._clock.now(),
            tier=tier,
            passed=passed,
        )
        await self._repository.save(row)


__all__ = ["ModelPinValidationLedger"]
