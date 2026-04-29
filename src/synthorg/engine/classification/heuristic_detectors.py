"""Heuristic detector protocol implementations.

Wraps the existing pure-function detectors from ``detectors.py`` as
``Detector`` protocol implementations, enabling them to be discovered
and dispatched by the pluggable classification pipeline.
"""

from typing import TYPE_CHECKING, Final

from synthorg.budget.coordination_config import (
    DetectionScope,
    ErrorCategory,
)
from synthorg.engine.classification.detectors import (
    detect_context_omissions,
    detect_coordination_failures,
    detect_logical_contradictions,
    detect_numerical_drift,
)

if TYPE_CHECKING:
    from synthorg.engine.classification.models import ErrorFinding
    from synthorg.engine.classification.protocol import DetectionContext

_SAME_TASK_ONLY: frozenset[DetectionScope] = frozenset(
    {DetectionScope.SAME_TASK},
)

# Default tolerance for numerical drift detection. 5% is the operator
# baseline; tighter thresholds are typically needed for financial or
# scientific tasks and are configured per detector instance.
_DEFAULT_NUMERICAL_DRIFT_PERCENT: Final[float] = 5.0


class HeuristicContradictionDetector:
    """Heuristic detector for logical contradictions.

    Wraps ``detect_logical_contradictions`` as a ``Detector``
    protocol implementation.
    """

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        return ErrorCategory.LOGICAL_CONTRADICTION

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        return _SAME_TASK_ONLY

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Run contradiction detection on the conversation.

        Args:
            context: Detection context with execution data.

        Returns:
            Tuple of contradiction findings.
        """
        conversation = context.execution_result.context.conversation
        return detect_logical_contradictions(conversation)


class HeuristicNumericalDriftDetector:
    """Heuristic detector for numerical value drift.

    Wraps ``detect_numerical_drift`` as a ``Detector`` protocol
    implementation.

    Args:
        threshold_percent: Maximum allowed drift percentage
            (default 5.0).
    """

    def __init__(
        self,
        *,
        threshold_percent: float = _DEFAULT_NUMERICAL_DRIFT_PERCENT,
    ) -> None:
        self._threshold_percent = threshold_percent

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        return ErrorCategory.NUMERICAL_DRIFT

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        return _SAME_TASK_ONLY

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Run numerical drift detection on the conversation.

        Args:
            context: Detection context with execution data.

        Returns:
            Tuple of drift findings.
        """
        conversation = context.execution_result.context.conversation
        return detect_numerical_drift(
            conversation,
            threshold_percent=self._threshold_percent,
        )


class HeuristicContextOmissionDetector:
    """Heuristic detector for context entity omissions.

    Wraps ``detect_context_omissions`` as a ``Detector`` protocol
    implementation.
    """

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        return ErrorCategory.CONTEXT_OMISSION

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        return _SAME_TASK_ONLY

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Run context omission detection on the conversation.

        Args:
            context: Detection context with execution data.

        Returns:
            Tuple of omission findings.
        """
        conversation = context.execution_result.context.conversation
        return detect_context_omissions(conversation)


class HeuristicCoordinationFailureDetector:
    """Heuristic detector for coordination failures.

    Wraps ``detect_coordination_failures`` as a ``Detector``
    protocol implementation.
    """

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        return ErrorCategory.COORDINATION_FAILURE

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        return _SAME_TASK_ONLY

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Run coordination failure detection.

        Args:
            context: Detection context with execution data.

        Returns:
            Tuple of coordination failure findings.
        """
        er = context.execution_result
        return detect_coordination_failures(
            er.context.conversation,
            er.turns,
        )
