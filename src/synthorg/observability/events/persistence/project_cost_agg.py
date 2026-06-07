# module-kind: declarative
"""Persistence event constants for the project_cost_agg sub-domain."""

from typing import Final

PERSISTENCE_PROJECT_COST_AGG_INCREMENTED: Final[str] = (
    "persistence.project_cost_agg.incremented"
)
PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED: Final[str] = (
    "persistence.project_cost_agg.increment_failed"
)
PERSISTENCE_PROJECT_COST_AGG_FETCHED: Final[str] = (
    "persistence.project_cost_agg.fetched"
)
PERSISTENCE_PROJECT_COST_AGG_FETCH_FAILED: Final[str] = (
    "persistence.project_cost_agg.fetch_failed"
)
PERSISTENCE_PROJECT_COST_AGG_DESERIALIZE_FAILED: Final[str] = (
    "persistence.project_cost_agg.deserialize_failed"
)
PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING: Final[str] = (
    "persistence.project_cost_agg.currency_pin_missing"
)
