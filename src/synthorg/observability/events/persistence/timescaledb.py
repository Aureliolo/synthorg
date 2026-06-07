# module-kind: declarative
"""Persistence event constants for the timescaledb sub-domain (hypertables)."""

from typing import Final

PERSISTENCE_TIMESCALEDB_UNAVAILABLE: Final[str] = "persistence.timescaledb.unavailable"
PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED: Final[str] = (
    "persistence.timescaledb.hypertable_created"
)
PERSISTENCE_TIMESCALEDB_SETUP_FAILED: Final[str] = (
    "persistence.timescaledb.setup_failed"
)
