"""Canonical action-type risk map + the map-backed classifier.

One home for the default ``ActionType -> ApprovalRiskLevel`` taxonomy and
the single classifier that reads it, so the security-rules and
timeout-policy subsystems can never silently diverge. The only per-site
variance is the base map (default vs operator-supplied), the optional
overlay, and the miss-log event name, all passed at construction.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import TIMEOUT_UNKNOWN_ACTION_TYPE
from synthorg.security.autonomy.enums import ActionType

logger = get_logger(__name__)

# One-step elevation ladder (CRITICAL is the ceiling). Shared by the
# workload-adaptive and time-based classifiers so "elevate one tier"
# means the same thing everywhere.
_ELEVATION: Final[MappingProxyType[ApprovalRiskLevel, ApprovalRiskLevel]] = (
    MappingProxyType(
        {
            ApprovalRiskLevel.LOW: ApprovalRiskLevel.MEDIUM,
            ApprovalRiskLevel.MEDIUM: ApprovalRiskLevel.HIGH,
            ApprovalRiskLevel.HIGH: ApprovalRiskLevel.CRITICAL,
            ApprovalRiskLevel.CRITICAL: ApprovalRiskLevel.CRITICAL,
        }
    )
)


def elevate_one_tier(level: ApprovalRiskLevel) -> ApprovalRiskLevel:
    """Return *level* raised one risk tier (``CRITICAL`` is the ceiling)."""
    return _ELEVATION[level]


# The single source of truth for default action-type risk tiers,
# consumed by both the security-rules baseline classifier and the
# tiered-timeout default classifier.
DEFAULT_RISK_MAP: Final[MappingProxyType[str, ApprovalRiskLevel]] = MappingProxyType(
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
        ActionType.EXTERNAL_DATA_REQUEST: ApprovalRiskLevel.HIGH,
        ActionType.BUDGET_EXCEED: ApprovalRiskLevel.HIGH,
        ActionType.TOOL_CREATE: ApprovalRiskLevel.HIGH,
        # MEDIUM
        ActionType.CODE_CREATE: ApprovalRiskLevel.MEDIUM,
        ActionType.CODE_WRITE: ApprovalRiskLevel.MEDIUM,
        ActionType.CODE_REFACTOR: ApprovalRiskLevel.MEDIUM,
        ActionType.VCS_COMMIT: ApprovalRiskLevel.MEDIUM,
        ActionType.ARCH_DECIDE: ApprovalRiskLevel.MEDIUM,
        ActionType.KNOWLEDGE_INGEST: ApprovalRiskLevel.MEDIUM,
        ActionType.RESEARCH_RUN: ApprovalRiskLevel.MEDIUM,
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
        ActionType.MEMORY_READ: ApprovalRiskLevel.LOW,
        ActionType.KNOWLEDGE_REINDEX: ApprovalRiskLevel.LOW,
        ActionType.BROWSER_NAVIGATE: ApprovalRiskLevel.LOW,
        ActionType.BROWSER_SCREENSHOT: ApprovalRiskLevel.LOW,
        ActionType.BROWSER_DIFF: ApprovalRiskLevel.LOW,
        ActionType.BROWSER_ACCESSIBILITY_SCAN: ApprovalRiskLevel.LOW,
        ActionType.BROWSER_SPEC: ApprovalRiskLevel.LOW,
        ActionType.DESKTOP_LAUNCH: ApprovalRiskLevel.MEDIUM,
        ActionType.DESKTOP_CLICK: ApprovalRiskLevel.LOW,
        ActionType.DESKTOP_TYPE: ApprovalRiskLevel.LOW,
        ActionType.DESKTOP_KEY: ApprovalRiskLevel.LOW,
        ActionType.DESKTOP_SCREENSHOT: ApprovalRiskLevel.LOW,
        ActionType.DESKTOP_SCROLL: ApprovalRiskLevel.LOW,
    }
)

# Validate exhaustiveness at module load time -- log a warning for any
# ActionType members missing from the default map.
_missing_action_types = {m.value for m in ActionType} - set(DEFAULT_RISK_MAP)
if _missing_action_types:
    logger.warning(
        TIMEOUT_UNKNOWN_ACTION_TYPE,
        missing_types=sorted(_missing_action_types),
        note=(
            "ActionType members missing from DEFAULT_RISK_MAP -- "
            "they will default to HIGH at classify() time"
        ),
    )
del _missing_action_types


class MapBackedRiskClassifier:
    """Classify action types to risk tiers from a configured map.

    Unknown action types fail safe to ``HIGH`` (DESIGN_SPEC D19): a
    taxonomy gap must never silently downgrade an action's risk.

    Args:
        base_map: Starting map. ``DEFAULT_RISK_MAP`` for the default /
            rules classifiers (merge semantics); ``None`` for the
            operator-configurable classifier (the overlay is the whole
            map, replace semantics).
        custom_map: Optional overrides applied on top of ``base_map``.
        miss_event: Observability event name logged when an action type
            is absent and the fail-safe ``HIGH`` is returned.
    """

    def __init__(
        self,
        *,
        base_map: Mapping[str, ApprovalRiskLevel] | None,
        custom_map: Mapping[str, ApprovalRiskLevel] | None = None,
        miss_event: str,
    ) -> None:
        merged: dict[str, ApprovalRiskLevel] = dict(base_map) if base_map else {}
        if custom_map:
            merged.update(custom_map)
        self._risk_map: MappingProxyType[str, ApprovalRiskLevel] = MappingProxyType(
            merged
        )
        self._miss_event = miss_event

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Classify an action type's risk tier; unknown -> HIGH (D19).

        Args:
            action_type: The ``category:action`` string.

        Returns:
            The mapped risk tier, or ``HIGH`` for an unknown type.
        """
        result = self._risk_map.get(action_type)
        if result is None:
            logger.warning(
                self._miss_event,
                action_type=action_type,
                fallback_tier="high",
            )
            return ApprovalRiskLevel.HIGH
        return result


def default_risk_classifier(
    *,
    miss_event: str,
    custom_map: Mapping[str, ApprovalRiskLevel] | None = None,
) -> MapBackedRiskClassifier:
    """Build a classifier over :data:`DEFAULT_RISK_MAP` (merge semantics).

    The single canonical constructor for the security-rules baseline and
    the tiered-timeout default classifiers; they differ only by
    *miss_event*.

    Returns:
        A ``MapBackedRiskClassifier`` over the default map plus overrides.
    """
    return MapBackedRiskClassifier(
        base_map=DEFAULT_RISK_MAP,
        custom_map=custom_map,
        miss_event=miss_event,
    )
