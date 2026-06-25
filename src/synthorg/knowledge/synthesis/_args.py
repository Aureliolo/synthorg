"""Typed args model for ingesting the synthesiser's LLM structured output.

The synthesiser emits JSON validated against these frozen models via
:func:`synthorg.core.boundary.parse_typed` at the model boundary, so malformed
or hallucinated shapes are rejected with a logged, safe error rather than
propagating into the answer.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.enums import KnowledgeClaimType
from synthorg.knowledge.models import AnswerText, ClaimText


class KnowledgeSynthClaimOut(BaseModel):
    """One synthesised claim citing chunks by ref_id."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    text: ClaimText = Field(description="The assertion")
    claim_type: KnowledgeClaimType = Field(description="Nature of the assertion")
    confidence: float = Field(ge=0.0, le=1.0, description="Synthesiser confidence")
    ref_ids: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Reference ids of the chunks backing this claim",
    )


class KnowledgeSynthesisOutput(BaseModel):
    """The synthesiser LLM's structured output."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    answer: AnswerText = Field(description="The prose answer to the question")
    claims: tuple[KnowledgeSynthClaimOut, ...] = Field(
        min_length=1,
        description="Cited claims comprising the answer body",
    )
