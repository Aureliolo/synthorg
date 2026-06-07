"""Research subsystem enumerations."""

from enum import StrEnum


class ResearchSourceType(StrEnum):
    """Origin of a research retrieval source.

    Drives ``RetrievalSource`` selection in the research subsystem. A
    ``KNOWLEDGE`` source queries the internal knowledge substrate; ``WEB``,
    ``ACADEMIC``, and ``CODE`` each route through a vendor-agnostic external
    search provider injected at runtime.
    """

    KNOWLEDGE = "knowledge"
    WEB = "web"
    ACADEMIC = "academic"
    CODE = "code"


class ClaimType(StrEnum):
    """Nature of a synthesised research claim.

    Classifies each assertion in a research report so the deliverable can
    distinguish sourced facts from interpretive analysis, recommendations,
    and cross-source comparisons.
    """

    FACT = "fact"
    ANALYSIS = "analysis"
    RECOMMENDATION = "recommendation"
    COMPARISON = "comparison"


class ResearchRunStatus(StrEnum):
    """Lifecycle state of a research run.

    A run advances through planning, retrieval, credibility triage,
    deduplication, and synthesis to ``COMPLETED``; any stage failure marks
    the run ``FAILED`` with a safe error description.
    """

    PLANNING = "planning"
    RETRIEVING = "retrieving"
    TRIAGING = "triaging"
    DEDUPLICATING = "deduplicating"
    SYNTHESISING = "synthesising"
    COMPLETED = "completed"
    FAILED = "failed"
