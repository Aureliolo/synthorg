# module-kind: declarative
"""Persistence event constants for the cost_forecast sub-domain."""

from typing import Final

PERSISTENCE_COST_FORECAST_SAVED: Final[str] = "persistence.cost_forecast.saved"
PERSISTENCE_COST_FORECAST_FETCHED: Final[str] = "persistence.cost_forecast.fetched"
PERSISTENCE_COST_FORECAST_LISTED: Final[str] = "persistence.cost_forecast.listed"
PERSISTENCE_COST_FORECAST_FAILED: Final[str] = "persistence.cost_forecast.failed"
