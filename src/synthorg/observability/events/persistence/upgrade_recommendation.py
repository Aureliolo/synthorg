# module-kind: declarative
"""Persistence event constants for the upgrade_recommendation sub-domain."""

from typing import Final

PERSISTENCE_UPGRADE_RECOMMENDATION_FETCHED: Final[str] = (
    "persistence.upgrade_recommendation.fetched"
)
PERSISTENCE_UPGRADE_RECOMMENDATION_LISTED: Final[str] = (
    "persistence.upgrade_recommendation.listed"
)
PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED: Final[str] = (
    "persistence.upgrade_recommendation.failed"
)
