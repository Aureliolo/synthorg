# module-kind: code
"""Reading the serviceability boundaries an operator set.

Kept apart from :mod:`synthorg.providers.serviceability`, which is pure
aggregation and must stay callable with no settings backend at all (the
tracker aggregates in tests and at boot before a resolver exists).

The read is live and per call rather than snapshotted, because these
boundaries decide which pairs are skipped and which agents read
unavailable: an operator who widens the window after an incident should see
the next request answer under the new one, not after a restart.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_SERVICEABILITY_THRESHOLDS_UNRESOLVED,
)
from synthorg.providers.serviceability import (
    DEFAULT_THRESHOLDS,
    ServiceabilityThresholds,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_NAMESPACE = SettingNamespace.PROVIDERS


async def resolve_serviceability_thresholds(
    resolver: ConfigResolver | None,
) -> ServiceabilityThresholds:
    """Resolve the operator's serviceability boundaries.

    Fails safe to the registered defaults: a settings-backend hiccup must
    not widen a window silently, and it must not make every pair read
    unknown either. Both would change which work runs where, from an outage
    that has nothing to do with any provider.

    Args:
        resolver: Live config resolver, or ``None`` before one is wired.

    Returns:
        The resolved thresholds, or the registered defaults.
    """
    if resolver is None:
        return DEFAULT_THRESHOLDS
    try:
        return ServiceabilityThresholds(
            window_seconds=await resolver.get_float(
                _NAMESPACE, "serviceability_window_seconds"
            ),
            degraded_error_rate_percent=await resolver.get_float(
                _NAMESPACE, "serviceability_degraded_error_rate_percent"
            ),
            down_error_rate_percent=await resolver.get_float(
                _NAMESPACE, "serviceability_down_error_rate_percent"
            ),
            min_calls_for_verdict=await resolver.get_int(
                _NAMESPACE, "serviceability_min_calls_for_verdict"
            ),
            latch_lookback_seconds=await resolver.get_float(
                _NAMESPACE, "serviceability_latch_lookback_seconds"
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_SERVICEABILITY_THRESHOLDS_UNRESOLVED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="falling back to the registered serviceability defaults",
        )
        return DEFAULT_THRESHOLDS


__all__ = ["resolve_serviceability_thresholds"]
