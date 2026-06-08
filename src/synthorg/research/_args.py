"""Typed args models for ingesting LLM structured output.

Each LLM-backed strategy emits JSON that is validated against one of these
frozen models via :func:`synthorg.api.boundary.parse_typed` at the model
boundary, so malformed or hallucinated shapes are rejected with a logged,
safe error rather than propagating into the pipeline.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import (
    TitleText,
)
from synthorg.research.enums import ClaimType, ResearchSourceType
from synthorg.research.models import (
    AngleText,
    AuthorityLevel,
    ClaimText,
    IntentText,
    SummaryText,
)


class PlannedSubQuery(BaseModel):
    """One planner-emitted sub-query (pre-indexing)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_type: ResearchSourceType = Field(description="Target retrieval source")
    query_text: NotBlankStr = Field(description="Query string for the source")
    intent: IntentText = Field(description="Why this query helps answer the brief")


class PlannerOutput(BaseModel):
    """The query planner's structured output."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    research_angle: AngleText = Field(description="Lens guiding synthesis")
    sub_queries: tuple[PlannedSubQuery, ...] = Field(
        min_length=1,
        description="Targeted sub-queries in priority order",
    )


class TriageVerdictOut(BaseModel):
    """An LLM credibility verdict for one item, keyed by ref_id."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ref_id: NotBlankStr = Field(description="Item this verdict scores")
    authority: AuthorityLevel = Field(description="Coarse authority bucket")
    domain_alignment: float = Field(ge=0.0, le=1.0, description="On-topic degree")
    score: float = Field(ge=0.0, le=1.0, description="Composite credibility score")
    red_flags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Markers of low quality",
    )


class TriageOutput(BaseModel):
    """The credibility-triage LLM's structured output."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdicts: tuple[TriageVerdictOut, ...] = Field(
        default=(),
        description="One verdict per scored item",
    )


class SynthClaimOut(BaseModel):
    """One synthesised claim citing sources by ref_id."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    text: ClaimText = Field(description="The assertion")
    claim_type: ClaimType = Field(description="Nature of the assertion")
    confidence: float = Field(ge=0.0, le=1.0, description="Synthesiser confidence")
    ref_ids: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Reference ids of the sources backing this claim",
    )


class SynthesisOutput(BaseModel):
    """The synthesiser LLM's structured output."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: TitleText = Field(description="Report title")
    summary: SummaryText = Field(description="Executive summary")
    claims: tuple[SynthClaimOut, ...] = Field(
        min_length=1,
        description="Cited claims comprising the report body",
    )
