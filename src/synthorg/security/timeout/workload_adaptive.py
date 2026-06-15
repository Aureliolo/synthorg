"""Workload-adaptive risk-tier classifier (REWORK #9).

Wraps a base classifier and elevates the result one tier when the
system is under load -- an in-flight request count at or above a
configured threshold. The load signal is an injected
``Callable[[], int]`` (kept out of the frozen config) so this
classifier never owns a runtime dependency on the rate-limit
subsystem.
"""

from collections.abc import Callable

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.security.risk_map import elevate_one_tier
from synthorg.security.timeout.protocol import RiskTierClassifier


class WorkloadAdaptiveRiskClassifier:
    """Elevate one risk tier while in-flight load is at/above threshold.

    Args:
        base: The classifier whose verdict is elevated under load.
        inflight_probe: Returns the current in-flight request count.
        threshold: In-flight count at/above which the tier is raised
            one step (``CRITICAL`` is the ceiling).
    """

    def __init__(
        self,
        *,
        base: RiskTierClassifier,
        inflight_probe: Callable[[], int],
        threshold: int,
    ) -> None:
        self._base = base
        self._inflight_probe = inflight_probe
        self._threshold = threshold

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Classify, elevating one tier when in-flight load is high.

        Returns:
            The base risk level, elevated one tier when the in-flight
            probe meets or exceeds the threshold.
        """
        level = self._base.classify(action_type)
        if self._inflight_probe() >= self._threshold:
            return elevate_one_tier(level)
        return level
