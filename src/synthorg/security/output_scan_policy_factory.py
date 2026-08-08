"""Factory for creating output scan policy instances from configuration."""

from synthorg.core.effective_autonomy import EffectiveAutonomy
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

logger = get_logger(__name__)

#: Said once, at the point the untiered policy is built, so an operator can
#: see WHY a screen that belongs to no run responds at the strictest tier.
_UNTIERED_NOTE = (
    "output_scan_policy_type=autonomy_tiered has no autonomy to tier by "
    "(this screen belongs to no run); responding at the strictest tier"
)


def build_output_scan_policy(
    policy_type: OutputScanPolicyType,
    *,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> OutputScanResponsePolicy:
    """Create an output scan policy from its config enum value.

    Args:
        policy_type: Declarative policy selection from config.
        effective_autonomy: Resolved autonomy for the current run, or
            ``None`` for a screen that belongs to no run (the
            credentialed-MCP request path). Read only by
            ``AUTONOMY_TIERED``.

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
                    note=_UNTIERED_NOTE,
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
