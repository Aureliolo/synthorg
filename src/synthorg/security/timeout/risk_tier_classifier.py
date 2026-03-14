"""Configurable risk tier classifier for timeout policies."""

from types import MappingProxyType
from typing import Final

from synthorg.core.enums import ActionType, ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import TIMEOUT_UNKNOWN_ACTION_TYPE

logger = get_logger(__name__)

# Reuses the same risk assignments as security/rules/risk_classifier.py.
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

# Validate exhaustiveness at module load time — log a warning for any
# ActionType members missing from the default map.
_missing_action_types = {m.value for m in ActionType} - set(_DEFAULT_RISK_MAP)
if _missing_action_types:
    logger.warning(
        TIMEOUT_UNKNOWN_ACTION_TYPE,
        missing_types=sorted(_missing_action_types),
        note=(
            "ActionType members missing from _DEFAULT_RISK_MAP — "
            "they will default to HIGH at classify() time"
        ),
    )
del _missing_action_types


class DefaultRiskTierClassifier:
    """Maps action types to risk tiers for tiered timeout policies.

    Unknown action types default to HIGH (fail-safe per D19).

    Args:
        custom_map: Optional overrides for the default risk mapping.
    """

    def __init__(
        self,
        *,
        custom_map: dict[str, ApprovalRiskLevel] | None = None,
    ) -> None:
        if custom_map:
            merged = dict(_DEFAULT_RISK_MAP)
            merged.update(custom_map)
            self._risk_map = MappingProxyType(merged)
        else:
            self._risk_map = _DEFAULT_RISK_MAP

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Classify an action type's risk tier.

        Args:
            action_type: The ``category:action`` string.

        Returns:
            Risk tier. Defaults to HIGH for unknown types.
        """
        result = self._risk_map.get(action_type)
        if result is None:
            logger.warning(
                TIMEOUT_UNKNOWN_ACTION_TYPE,
                action_type=action_type,
                default_tier="high",
                note="unknown action type — defaulting to HIGH (D19)",
            )
            return ApprovalRiskLevel.HIGH
        return result
