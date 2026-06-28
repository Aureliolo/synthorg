"""Persisted record of a prompt class's last clean pin validation.

A :class:`ModelPinValidationRow` is the durable answer to "when was this
prompt class's model pin last validated against its tier?". The
pin-validation benchmark
(:mod:`synthorg.hr.evaluation.pin_validation_benchmark`) writes one row
per prompt class *only on a clean drift grade*, so a row's mere existence
means the pin passed and :attr:`validated_at` is the durable "last
validated" timestamp rather than a value baked at write time. There is no
``passed`` column: a failure is drift, surfaced in the benchmark result,
never stamped here, so the store cannot record (or be clobbered by) a
failed run. The audit dashboard reads these rows to surface pin freshness
per prompt purpose (the live counterpart to a prompt class's static
``ModelPinMetadata.model_version_pinned_at``).

The row is keyed by ``prompt_class_id`` (a :class:`PromptPurposeId`), and
its ``tier`` is a canonical :class:`~synthorg.budget.model_tier.TierName`,
so a non-canonical tier is rejected at construction rather than persisted.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.budget.model_tier import TierName
from synthorg.llm.prompt_purpose import PromptPurposeId


class ModelPinValidationRow(BaseModel):
    """A prompt class's most recent pin-validation result."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    prompt_class_id: PromptPurposeId = Field(
        description="Stable identifier for the validated prompt class",
    )
    validated_at: AwareDatetime = Field(
        description="When the pin was last validated against its tier",
    )
    tier: TierName = Field(description="The design tier validated against")


__all__ = ["ModelPinValidationRow"]
