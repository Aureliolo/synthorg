"""Hybrid credibility triage (shipped default).

Runs the deterministic heuristic over every item, then escalates only the
items that clear a cheap prefilter to the LLM for a refined verdict. Weak
items keep their heuristic verdict, so the LLM cost is spent where it
matters and obviously poor sources never reach it.
"""

from synthorg.research.constants import RESEARCH_HYBRID_PREFILTER_FACTOR
from synthorg.research.models import (
    ResearchBrief,
    RetrievedItem,
    SourceCredibility,
)
from synthorg.research.triage.heuristic import HeuristicCredibilityTriage
from synthorg.research.triage.llm import LlmCredibilityTriage


class HybridCredibilityTriage:
    """Heuristic prefilter followed by LLM triage on the survivors."""

    __slots__ = ("_heuristic", "_llm", "_prefilter_factor")

    def __init__(
        self,
        *,
        heuristic: HeuristicCredibilityTriage,
        llm: LlmCredibilityTriage,
        prefilter_factor: float = RESEARCH_HYBRID_PREFILTER_FACTOR,
    ) -> None:
        self._heuristic = heuristic
        self._llm = llm
        self._prefilter_factor = prefilter_factor

    async def triage(
        self,
        items: tuple[RetrievedItem, ...],
        *,
        brief: ResearchBrief,
    ) -> tuple[tuple[SourceCredibility, ...], float]:
        """Return one verdict per item and the LLM cost of the survivors."""
        heuristic_by_ref = {
            item.ref_id: self._heuristic.score(item, brief=brief) for item in items
        }
        floor = brief.min_credibility * self._prefilter_factor
        survivors = tuple(
            item for item in items if heuristic_by_ref[item.ref_id].score >= floor
        )
        llm_by_ref: dict[str, SourceCredibility] = {}
        cost = 0.0
        if survivors:
            llm_verdicts, cost = await self._llm.triage(survivors, brief=brief)
            llm_by_ref = {verdict.ref_id: verdict for verdict in llm_verdicts}
        results = tuple(
            llm_by_ref.get(item.ref_id, heuristic_by_ref[item.ref_id]) for item in items
        )
        return results, cost
