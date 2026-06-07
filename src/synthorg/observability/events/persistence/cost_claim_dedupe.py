# module-kind: declarative
"""Persistence event constants for the cost_claim_dedupe sub-domain."""

from typing import Final

PERSISTENCE_COST_CLAIM_DEDUPE_FAILED: Final[str] = (
    "persistence.cost_claim_dedupe.claim_failed"
)
PERSISTENCE_COST_CLAIM_DEDUPE_PRUNED: Final[str] = (
    "persistence.cost_claim_dedupe.pruned"
)
PERSISTENCE_COST_CLAIM_DEDUPE_PRUNE_FAILED: Final[str] = (
    "persistence.cost_claim_dedupe.prune_failed"
)
