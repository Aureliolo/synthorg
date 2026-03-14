"""Coordination error classification pipeline.

Re-exports the public API for error taxonomy classification —
models and the main pipeline entry point.
"""

from synthorg.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.engine.classification.pipeline import classify_execution_errors

__all__ = [
    "ClassificationResult",
    "ErrorFinding",
    "ErrorSeverity",
    "classify_execution_errors",
]
