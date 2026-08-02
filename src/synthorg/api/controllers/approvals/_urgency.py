# module-kind: code
"""Settings-backed resolution of the approval urgency thresholds.

Reads the two ``api.approval_urgency_*_seconds`` keys per request, validates
them as a pair, and falls back to the registry defaults when the settings
backend is unavailable or the operator stored an unusable combination. The
fallback logs once per transition, so a flapping backend does not spam.
"""

import asyncio
import math
from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_SETTINGS_BACKEND_RECOVERED,
    API_VALIDATION_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

_URGENCY_CRITICAL_FALLBACK_SECONDS: Final[float] = 3600.0
_URGENCY_HIGH_FALLBACK_SECONDS: Final[float] = 14400.0


_urgency_threshold_fallback_logged: bool = False


def _urgency_thresholds_fallback(reason: str) -> tuple[float, float]:
    """Log the fallback warning once and return the registry defaults.

    Idempotent: only the first transition into the fallback state
    emits a log line, so a flapping settings backend doesn't spam.

    Returns:
        Tuple of the declared element types.
    """
    global _urgency_threshold_fallback_logged  # noqa: PLW0603
    if not _urgency_threshold_fallback_logged:
        logger.warning(
            API_VALIDATION_FAILED,
            error=reason,
            critical_fallback=_URGENCY_CRITICAL_FALLBACK_SECONDS,
            high_fallback=_URGENCY_HIGH_FALLBACK_SECONDS,
        )
        _urgency_threshold_fallback_logged = True
    return _URGENCY_CRITICAL_FALLBACK_SECONDS, _URGENCY_HIGH_FALLBACK_SECONDS


def _validate_urgency_thresholds(
    critical: float,
    high: float,
) -> tuple[float, float]:
    """Validate resolved thresholds and emit the recovery log on success.

    Thresholds must be non-negative, finite, and ordered
    (``critical < high``); otherwise the urgency bucketing would
    misclassify every approval (a ``critical=high=0`` setting would
    mark everything as ``CRITICAL``).  Invalid values are treated
    identically to a backend outage so the fallback log fires and
    recovery is still possible.

    Returns:
        Tuple of the declared element types.
    """
    global _urgency_threshold_fallback_logged  # noqa: PLW0603
    if (
        not (math.isfinite(critical) and math.isfinite(high))
        or critical < 0
        or high < 0
        or critical >= high
    ):
        return _urgency_thresholds_fallback(
            "approval urgency thresholds are invalid"
            " (require 0 <= critical < high, both finite);"
            " using fallback"
        )
    if _urgency_threshold_fallback_logged:
        logger.info(
            API_SETTINGS_BACKEND_RECOVERED,
            setting="approval_urgency_thresholds",
            critical_seconds=critical,
            high_seconds=high,
        )
        _urgency_threshold_fallback_logged = False
    return critical, high


async def _resolve_urgency_thresholds(app_state: AppState) -> tuple[float, float]:
    """Read approval urgency thresholds from the settings backend.

    Falls back to the registry defaults (3600s critical / 14400s high)
    if the settings backend is unavailable.  Per-process log-once so a
    flapping settings backend does not spam the logs.

    Returns:
        Tuple of the declared element types.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return _urgency_thresholds_fallback(
            "no config resolver available; using approval urgency threshold fallbacks"
        )
    try:
        critical = await config_resolver_of(app_state).get_float(
            SettingNamespace.API.value, "approval_urgency_critical_seconds"
        )
        high = await config_resolver_of(app_state).get_float(
            SettingNamespace.API.value, "approval_urgency_high_seconds"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        return _urgency_thresholds_fallback(
            "failed to resolve approval urgency thresholds;"
            f" using fallback ({type(exc).__name__})"
        )
    return _validate_urgency_thresholds(critical, high)
