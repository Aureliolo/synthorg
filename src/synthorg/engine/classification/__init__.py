"""Coordination error classification pipeline.

Re-exports the public API for error taxonomy classification:
models, protocols, detectors, and the main pipeline entry point.
"""

from synthorg.engine.classification.composite import CompositeDetector
from synthorg.engine.classification.heuristic_detectors import (
    HeuristicContextOmissionDetector,
    HeuristicContradictionDetector,
    HeuristicCoordinationFailureDetector,
    HeuristicNumericalDriftDetector,
)
from synthorg.engine.classification.loaders import (
    SameTaskLoader,
    TaskTreeLoader,
)
from synthorg.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.engine.classification.pipeline import classify_execution_errors
from synthorg.engine.classification.protocol import (
    ClassificationSink,
    DetectionContext,
    Detector,
    ScopedContextLoader,
)
from synthorg.engine.classification.protocol_detectors import (
    AuthorityBreachDetector,
    DelegationProtocolDetector,
    ReviewPipelineProtocolDetector,
)

__all__ = [
    "AuthorityBreachDetector",
    "ClassificationResult",
    "ClassificationSink",
    "CompositeDetector",
    "DelegationProtocolDetector",
    "DetectionContext",
    "Detector",
    "ErrorFinding",
    "ErrorSeverity",
    "HeuristicContextOmissionDetector",
    "HeuristicContradictionDetector",
    "HeuristicCoordinationFailureDetector",
    "HeuristicNumericalDriftDetector",
    "ReviewPipelineProtocolDetector",
    "SameTaskLoader",
    "ScopedContextLoader",
    "TaskTreeLoader",
    "classify_execution_errors",
]
