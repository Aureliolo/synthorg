"""Operator-configurable risk-tier classifier (REWORK #9).

Classifies action types from an operator-defined map only. Unknown
action types fail safe to ``HIGH`` per ADR-0001 D19 -- an operator
taxonomy gap must never silently downgrade an action's risk.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import TIMEOUT_UNKNOWN_ACTION_TYPE

logger = get_logger(__name__)


class OperatorConfigurableRiskClassifier:
    """Risk tiers from an operator-defined ``action_type -> tier`` map.

    Args:
        operator_map: The operator's taxonomy. Copied into an immutable
            mapping at construction so later caller mutation cannot
            change classification mid-run.
    """

    def __init__(
        self,
        *,
        operator_map: Mapping[str, ApprovalRiskLevel],
    ) -> None:
        self._risk_map: MappingProxyType[str, ApprovalRiskLevel] = MappingProxyType(
            dict(operator_map)
        )

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Classify via the operator map; unknown -> HIGH (D19).

        Returns:
            The mapped risk level, or HIGH when the action type is
            absent from the operator map.
        """
        result = self._risk_map.get(action_type)
        if result is None:
            logger.warning(
                TIMEOUT_UNKNOWN_ACTION_TYPE,
                action_type=action_type,
                default_tier="high",
                note=(
                    "action type absent from operator risk map -- "
                    "defaulting to HIGH (D19)"
                ),
            )
            return ApprovalRiskLevel.HIGH
        return result
