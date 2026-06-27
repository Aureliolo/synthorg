"""Cross-cutting LLM helpers: prompt-purpose registry, model pinning.

:data:`PROMPT_PURPOSE_REGISTRY` is the single vocabulary of stable prompt
purpose ids. The same :class:`PromptPurposeId` is the identifier source
for two planned consumers: cost attribution (spend/latency sliced by
purpose) and model-pin validation (the value a
:attr:`ModelPinMetadata.prompt_class_id` carries), so both reference one
vocabulary instead of inventing their own strings.

:class:`ModelPinMetadata` is the schema for the model + sampling
parameters a prompt class pins.
"""

from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.prompt_purpose import (
    PROMPT_PURPOSE_REGISTRY,
    PromptPurpose,
    PromptPurposeCategory,
    PromptPurposeId,
    PromptPurposeRegistry,
    default_prompt_purpose_registry,
)

__all__ = (
    "PROMPT_PURPOSE_REGISTRY",
    "ModelPinMetadata",
    "PromptPurpose",
    "PromptPurposeCategory",
    "PromptPurposeId",
    "PromptPurposeRegistry",
    "default_prompt_purpose_registry",
)
