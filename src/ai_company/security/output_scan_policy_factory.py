"""Factory for creating output scan policy instances from configuration."""

from typing import TYPE_CHECKING

from ai_company.observability import get_logger
from ai_company.observability.events.security import (
    SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
)
from ai_company.security.config import OutputScanPolicyType
from ai_company.security.output_scan_policy import (
    AutonomyTieredPolicy,
    LogOnlyPolicy,
    OutputScanResponsePolicy,
    RedactPolicy,
    WithholdPolicy,
)

if TYPE_CHECKING:
    from ai_company.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)


def build_output_scan_policy(
    policy_type: OutputScanPolicyType,
    *,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> OutputScanResponsePolicy:
    """Create an output scan policy from its config enum value.

    Args:
        policy_type: Declarative policy selection from config.
        effective_autonomy: Resolved autonomy for the current run.
            Required when ``policy_type`` is ``AUTONOMY_TIERED``;
            ignored otherwise.

    Returns:
        A configured output scan response policy instance.

    Raises:
        TypeError: If ``policy_type`` is not a recognized enum member.
    """
    match policy_type:
        case OutputScanPolicyType.REDACT:
            return RedactPolicy()
        case OutputScanPolicyType.WITHHOLD:
            return WithholdPolicy()
        case OutputScanPolicyType.LOG_ONLY:
            return LogOnlyPolicy()
        case OutputScanPolicyType.AUTONOMY_TIERED:
            if effective_autonomy is None:
                logger.warning(
                    SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
                    policy_type=policy_type.value,
                    note="output_scan_policy_type=autonomy_tiered "
                    "but no effective_autonomy — "
                    "AutonomyTieredPolicy will fall back to "
                    "RedactPolicy",
                )
            return AutonomyTieredPolicy(
                effective_autonomy=effective_autonomy,
            )

    msg = f"Unknown output scan policy type: {policy_type!r}"  # type: ignore[unreachable]
    logger.warning(
        SECURITY_OUTPUT_SCAN_POLICY_APPLIED,
        policy_type=str(policy_type),
        note="Unknown output scan policy type",
    )
    raise TypeError(msg)
