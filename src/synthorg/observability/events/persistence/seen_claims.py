# module-kind: declarative
"""Persistence event constants for the seen_claims sub-domain (TaskClaim dedup)."""

from typing import Final

PERSISTENCE_SEEN_CLAIMS_MARK_FAILED: Final[str] = "persistence.seen_claims.mark_failed"
PERSISTENCE_SEEN_CLAIMS_LOOKUP_FAILED: Final[str] = (
    "persistence.seen_claims.lookup_failed"
)
PERSISTENCE_SEEN_CLAIMS_PRUNED: Final[str] = "persistence.seen_claims.pruned"
PERSISTENCE_SEEN_CLAIMS_PRUNE_FAILED: Final[str] = (
    "persistence.seen_claims.prune_failed"
)
