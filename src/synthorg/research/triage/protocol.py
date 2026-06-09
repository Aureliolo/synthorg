"""Credibility-triage protocol.

Triage scores each retrieved item for credibility relative to the brief
and marks whether it clears the brief's threshold. Implementations return
the verdicts plus any USD cost incurred (LLM-backed strategies).
"""

from typing import Protocol, runtime_checkable

from synthorg.research.models import (
    ResearchBrief,
    RetrievedItem,
    SourceCredibility,
)


@runtime_checkable
class CredibilityTriage(Protocol):
    """Scores source credibility and flags items below the threshold."""

    async def triage(
        self,
        items: tuple[RetrievedItem, ...],
        *,
        brief: ResearchBrief,
    ) -> tuple[tuple[SourceCredibility, ...], float]:
        """Return one verdict per item and the USD cost of triage.

        Args:
            items: Retrieved candidates to score.
            brief: The research brief (supplies the credibility threshold).

        Returns:
            A ``(verdicts, cost)`` pair with exactly one verdict per
            input item, each ``passed`` flag set against
            ``brief.min_credibility``.
        """
        ...
