"""Coordination error classification pipeline.

Re-exports the public API for error taxonomy classification —
models and the main pipeline entry point.
"""

from ai_company.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
    ErrorSeverity,
)
from ai_company.engine.classification.pipeline import classify_execution_errors

__all__ = [
    "ClassificationResult",
    "ErrorFinding",
    "ErrorSeverity",
    "classify_execution_errors",
]
