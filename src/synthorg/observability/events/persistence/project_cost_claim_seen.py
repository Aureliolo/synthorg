# module-kind: declarative
"""Persistence event constants for project-cost-claim dedup (restart-safe billing)."""

from typing import Final

PERSISTENCE_COST_CLAIM_SEEN_MARK_FAILED: Final[str] = (
    "persistence.cost_claim_seen.mark_failed"
)
PERSISTENCE_COST_CLAIM_SEEN_LOOKUP_FAILED: Final[str] = (
    "persistence.cost_claim_seen.lookup_failed"
)
PERSISTENCE_COST_CLAIM_SEEN_PRUNED: Final[str] = "persistence.cost_claim_seen.pruned"
PERSISTENCE_COST_CLAIM_SEEN_PRUNE_FAILED: Final[str] = (
    "persistence.cost_claim_seen.prune_failed"
)
