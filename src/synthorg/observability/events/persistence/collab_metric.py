# module-kind: declarative
"""Persistence event constants for the collab_metric sub-domain."""

from typing import Final

PERSISTENCE_COLLAB_METRIC_SAVED: Final[str] = "persistence.collab_metric.saved"
PERSISTENCE_COLLAB_METRIC_SAVE_FAILED: Final[str] = (
    "persistence.collab_metric.save_failed"
)
PERSISTENCE_COLLAB_METRIC_QUERIED: Final[str] = "persistence.collab_metric.queried"
PERSISTENCE_COLLAB_METRIC_QUERY_FAILED: Final[str] = (
    "persistence.collab_metric.query_failed"
)
PERSISTENCE_COLLAB_METRIC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.collab_metric.deserialize_failed"
)
