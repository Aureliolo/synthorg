# module-kind: declarative
"""Persistence event constants for the task_metric sub-domain."""

from typing import Final

PERSISTENCE_TASK_METRIC_SAVED: Final[str] = "persistence.task_metric.saved"
PERSISTENCE_TASK_METRIC_SAVE_FAILED: Final[str] = "persistence.task_metric.save_failed"
PERSISTENCE_TASK_METRIC_QUERIED: Final[str] = "persistence.task_metric.queried"
PERSISTENCE_TASK_METRIC_QUERY_FAILED: Final[str] = (
    "persistence.task_metric.query_failed"
)
PERSISTENCE_TASK_METRIC_DESERIALIZE_FAILED: Final[str] = (
    "persistence.task_metric.deserialize_failed"
)
