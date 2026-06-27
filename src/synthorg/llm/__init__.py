"""Cross-cutting LLM helpers: prompt-purpose registry, model pinning.

:data:`PROMPT_PURPOSE_REGISTRY` is the single source of stable prompt
purpose ids. The same :class:`PromptPurposeId` feeds cost attribution
(spend/latency sliced by purpose) and model-pin validation (it is the
value carried by :attr:`ModelPinMetadata.prompt_class_id`), so the two
consumers share one identifier vocabulary instead of inventing strings.

:class:`ModelPinMetadata` is the source of truth for the model + sampling
parameters a prompt class commits to.
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
