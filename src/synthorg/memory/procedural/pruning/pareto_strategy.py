"""Pareto frontier-based memory pruning strategy.

Removes entries that are dominated on multiple dimensions (relevance,
recency). Entries on the Pareto frontier are preserved.
"""

from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry
from synthorg.observability import get_logger

logger = get_logger(__name__)
_DEFAULT_MAX_ENTRIES: Final[int] = 100


class ParetoPruningStrategy:
    """Multi-dimensional Pareto frontier pruning.

    Identifies entries NOT on the Pareto frontier (dominated entries)
    for removal. Keeps entries up to max_entries on the frontier.

    Dimensions:
        - relevance_score: higher is better (0.0 to 1.0)
        - recency: newer is better (based on created_at)

    Attributes:
        max_entries: Maximum entries to keep (default 100).
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        """Initialize Pareto pruning strategy.

        Args:
            max_entries: Maximum entries to keep (default 100).
        """
        self.max_entries = max_entries

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "pareto"

    async def prune(
        self,
        *,
        agent_id: NotBlankStr,  # noqa: ARG002
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[str, ...]:
        """Identify dominated entries for removal via Pareto frontier.

        Only prunes if entries exceed max_entries. Returns IDs of entries
        NOT on the Pareto frontier.

        Args:
            agent_id: Agent whose memories are being pruned.
            entries: Current procedural memory entries.

        Returns:
            Tuple of memory entry IDs to remove.
        """
        if len(entries) <= self.max_entries:
            return ()

        # Build Pareto frontier
        frontier = self._compute_pareto_frontier(entries)

        # If frontier itself exceeds max_entries, keep only most recent
        if len(frontier) > self.max_entries:
            frontier = sorted(
                frontier,
                key=lambda e: e.created_at,
                reverse=True,
            )[: self.max_entries]

        frontier_ids = {str(e.id) for e in frontier}

        # Return non-frontier entry IDs
        to_remove = [
            str(entry.id) for entry in entries if str(entry.id) not in frontier_ids
        ]
        return tuple(to_remove)

    def _compute_pareto_frontier(
        self,
        entries: tuple[MemoryEntry, ...],
    ) -> list[MemoryEntry]:
        """Compute Pareto frontier on relevance and recency.

        An entry is on the frontier if no other entry dominates it
        (higher relevance AND higher recency).

        Args:
            entries: Memory entries to evaluate.

        Returns:
            Entries on the Pareto frontier.
        """
        frontier: list[MemoryEntry] = []

        for candidate in entries:
            # Get candidate scores
            candidate_rel = self._get_relevance(candidate)
            candidate_rec = self._get_recency_score(candidate, entries)

            # Check if candidate is dominated by any frontier member
            is_dominated = False
            for frontier_entry in frontier:
                frontier_rel = self._get_relevance(frontier_entry)
                frontier_rec = self._get_recency_score(frontier_entry, entries)

                # Domination: strictly better on both dimensions
                if (
                    frontier_rel >= candidate_rel
                    and frontier_rec >= candidate_rec
                    and (frontier_rel > candidate_rel or frontier_rec > candidate_rec)
                ):
                    is_dominated = True
                    break

            if not is_dominated:
                # Remove any frontier members dominated by candidate
                frontier = [
                    f
                    for f in frontier
                    if not (
                        candidate_rel >= self._get_relevance(f)
                        and candidate_rec >= self._get_recency_score(f, entries)
                        and (
                            candidate_rel > self._get_relevance(f)
                            or candidate_rec > self._get_recency_score(f, entries)
                        )
                    )
                ]
                frontier.append(candidate)

        return frontier

    def _get_relevance(self, entry: MemoryEntry) -> float:
        """Get relevance score (0.0 if None).

        Returns:
            Result of type ``float``.
        """
        return entry.relevance_score or 0.0

    def _get_recency_score(
        self,
        entry: MemoryEntry,
        all_entries: tuple[MemoryEntry, ...],
    ) -> float:
        """Compute recency as normalized score (0.0 oldest, 1.0 newest).

        Returns:
            Result of type ``float``.
        """
        if not all_entries:
            return 0.0

        # Find oldest and newest
        timestamps = [e.created_at for e in all_entries]
        oldest = min(timestamps)
        newest = max(timestamps)

        if oldest == newest:
            return 1.0

        # Normalize to [0, 1]
        age_span = (newest - oldest).total_seconds()
        entry_age = (entry.created_at - oldest).total_seconds()
        return entry_age / age_span
