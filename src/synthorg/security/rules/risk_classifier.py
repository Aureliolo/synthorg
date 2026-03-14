"""Risk classifier — maps action types to default risk levels."""

from types import MappingProxyType
from typing import Final

from synthorg.core.enums import ActionType, ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_RISK_FALLBACK

logger = get_logger(__name__)

_DEFAULT_RISK_MAP: Final[MappingProxyType[str, ApprovalRiskLevel]] = MappingProxyType(
    {
        # CRITICAL
        ActionType.DEPLOY_PRODUCTION: ApprovalRiskLevel.CRITICAL,
        ActionType.DB_ADMIN: ApprovalRiskLevel.CRITICAL,
        ActionType.ORG_FIRE: ApprovalRiskLevel.CRITICAL,
        # HIGH
        ActionType.DEPLOY_STAGING: ApprovalRiskLevel.HIGH,
        ActionType.DB_MUTATE: ApprovalRiskLevel.HIGH,
        ActionType.CODE_DELETE: ApprovalRiskLevel.HIGH,
        ActionType.VCS_PUSH: ApprovalRiskLevel.HIGH,
        ActionType.COMMS_EXTERNAL: ApprovalRiskLevel.HIGH,
        ActionType.BUDGET_EXCEED: ApprovalRiskLevel.HIGH,
        # MEDIUM
        ActionType.CODE_CREATE: ApprovalRiskLevel.MEDIUM,
        ActionType.CODE_WRITE: ApprovalRiskLevel.MEDIUM,
        ActionType.CODE_REFACTOR: ApprovalRiskLevel.MEDIUM,
        ActionType.VCS_COMMIT: ApprovalRiskLevel.MEDIUM,
        ActionType.ARCH_DECIDE: ApprovalRiskLevel.MEDIUM,
        ActionType.ORG_HIRE: ApprovalRiskLevel.MEDIUM,
        ActionType.ORG_PROMOTE: ApprovalRiskLevel.MEDIUM,
        ActionType.BUDGET_SPEND: ApprovalRiskLevel.MEDIUM,
        # LOW
        ActionType.CODE_READ: ApprovalRiskLevel.LOW,
        ActionType.VCS_READ: ApprovalRiskLevel.LOW,
        ActionType.TEST_RUN: ApprovalRiskLevel.LOW,
        ActionType.TEST_WRITE: ApprovalRiskLevel.LOW,
        ActionType.DOCS_WRITE: ApprovalRiskLevel.LOW,
        ActionType.VCS_BRANCH: ApprovalRiskLevel.LOW,
        ActionType.COMMS_INTERNAL: ApprovalRiskLevel.LOW,
        ActionType.DB_QUERY: ApprovalRiskLevel.LOW,
    }
)


class RiskClassifier:
    """Maps action types to default risk levels.

    Used by the rule engine when no specific rule triggers, to
    assign a baseline risk level based on the action type.
    """

    def __init__(
        self,
        *,
        custom_risk_map: dict[str, ApprovalRiskLevel] | None = None,
    ) -> None:
        """Initialize with optional custom risk overrides.

        Args:
            custom_risk_map: Additional or overriding risk mappings.
        """
        if custom_risk_map:
            merged = dict(_DEFAULT_RISK_MAP)
            merged.update(custom_risk_map)
            self._risk_map = MappingProxyType(merged)
        else:
            self._risk_map = _DEFAULT_RISK_MAP

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Return the risk level for an action type.

        Falls back to ``HIGH`` for unknown action types (fail-safe per
        DESIGN_SPEC D19).

        Args:
            action_type: The ``category:action`` string.

        Returns:
            The assessed risk level.
        """
        result = self._risk_map.get(action_type)
        if result is None:
            logger.warning(
                SECURITY_RISK_FALLBACK,
                action_type=action_type,
                fallback="HIGH",
            )
            return ApprovalRiskLevel.HIGH
        return result
