"""Synthesiser protocol.

A synthesiser turns the retained, deduplicated sources into a
:class:`ResearchReport` whose every claim cites at least one source.
Implementations return the report plus any USD cost incurred.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.research.models import (
        ResearchBrief,
        ResearchQueryPlan,
        ResearchReport,
        RetrievedItem,
    )


@runtime_checkable
class Synthesizer(Protocol):
    """Produces a citation-backed report from curated sources."""

    async def synthesize(
        self,
        brief: ResearchBrief,
        plan: ResearchQueryPlan,
        sources: tuple[RetrievedItem, ...],
        *,
        sources_consulted: int,
    ) -> tuple[ResearchReport, float]:
        """Return a cited report and the USD cost of producing it.

        Args:
            brief: The research brief.
            plan: The query plan that drove retrieval.
            sources: Retained, deduplicated items the report may cite.
            sources_consulted: Total items retrieved before triage, for the
                report's methodology metrics.

        Returns:
            A ``(report, cost)`` pair. Every claim resolves to at least
            one of *sources*; an unsourced claim raises
            :class:`~synthorg.research.errors.ResearchSynthesisError`.
        """
        ...
