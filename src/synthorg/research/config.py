"""Configuration for the research subsystem.

Carries the pluggable strategy discriminators (query planner, credibility
triage, deduplicator, synthesiser) and the enable flag. Numeric tuning
knobs live in :mod:`synthorg.research.constants`; brief-level limits live
on :class:`~synthorg.research.models.ResearchBrief`.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QueryPlannerKind = Literal["llm"]
"""Discriminator for the query-planning strategy. ``llm`` is the only
shipped implementation; new strategies extend this union AND the factory
in :func:`synthorg.research.factory.build_research_service` in lockstep,
so an unmatched discriminator fails at wiring time, not at first run."""

CredibilityTriageKind = Literal["hybrid", "heuristic", "llm"]
"""Discriminator for the source-credibility triage strategy. ``hybrid``
(deterministic heuristic prefilter then LLM triage on survivors) is the
default; ``heuristic`` and ``llm`` select a single-strategy mode."""

DeduplicatorKind = Literal["lexical", "embedding"]
"""Discriminator for the deduplication strategy. ``lexical`` (content-hash
plus canonical-URL plus token-shingle Jaccard) is the deterministic,
replay-friendly default; ``embedding`` reuses the memory backend's vector
similarity when one is wired."""

SynthesizerKind = Literal["llm"]
"""Discriminator for the synthesis strategy. ``llm`` is the only shipped
implementation; the synthesiser cites sources by reference id and a binder
validates every citation resolves before the report is emitted."""


class ResearchConfig(BaseModel):
    """Top-level research-subsystem configuration.

    Disabled by default: the subsystem is wired only when an operator turns
    it on, mirroring other opt-in subsystems. The discriminators select
    pluggable strategies via the service factory.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether the research subsystem is wired at startup",
    )
    query_planner: QueryPlannerKind = Field(
        default="llm",
        description="Query-planning strategy discriminator",
    )
    credibility_triage: CredibilityTriageKind = Field(
        default="hybrid",
        description="Source-credibility triage strategy discriminator",
    )
    deduplicator: DeduplicatorKind = Field(
        default="lexical",
        description="Deduplication strategy discriminator",
    )
    synthesizer: SynthesizerKind = Field(
        default="llm",
        description="Synthesis strategy discriminator",
    )
