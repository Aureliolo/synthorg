# module-kind: declarative
"""Persistence event constants for the evaluation_report sub-domain."""

from typing import Final

PERSISTENCE_EVALUATION_REPORT_SAVE_FAILED: Final[str] = (
    "persistence.evaluation_report.save_failed"
)
PERSISTENCE_EVALUATION_REPORT_QUERY_FAILED: Final[str] = (
    "persistence.evaluation_report.query_failed"
)
PERSISTENCE_EVALUATION_REPORT_DELETE_FAILED: Final[str] = (
    "persistence.evaluation_report.delete_failed"
)
PERSISTENCE_EVALUATION_REPORT_DESERIALIZE_FAILED: Final[str] = (
    "persistence.evaluation_report.deserialize_failed"
)
