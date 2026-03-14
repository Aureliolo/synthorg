"""Factory for creating output scan policy instances from configuration."""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_CONFIG_LOADED,
    SECURITY_INTERCEPTOR_ERROR,
)
from synthorg.security.config import OutputScanPolicyType
from synthorg.security.output_scan_policy import (
    AutonomyTieredPolicy,
    LogOnlyPolicy,
    OutputScanResponsePolicy,
    RedactPolicy,
    WithholdPolicy,
)

if TYPE_CHECKING:
    from synthorg.security.autonomy.models import EffectiveAutonomy

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
            Used when ``policy_type`` is ``AUTONOMY_TIERED``. If
            ``None`` in that case, a warning is logged and the policy
            will fall back to ``RedactPolicy``. Ignored for other
            policy types.

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
                    SECURITY_CONFIG_LOADED,
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
    logger.error(
        SECURITY_INTERCEPTOR_ERROR,
        policy_type=str(policy_type),
        note="Unknown output scan policy type",
    )
    raise TypeError(msg)
