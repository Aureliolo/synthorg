"""Shared DTOs, urgency resolution, and fetch helpers for approvals.

Pure helper module consumed by both the approvals query and decision
controllers: the urgency-threshold resolution (settings-backed with a
log-once fallback), the urgency-enriched response DTO + its conversion,
and the approval-store fetch-or-404 helper. No Litestar surface.
"""

import asyncio
import math
from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
    API_SETTINGS_BACKEND_RECOVERED,
    API_VALIDATION_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

_URGENCY_CRITICAL_FALLBACK_SECONDS: float = 3600.0
_URGENCY_HIGH_FALLBACK_SECONDS: float = 14400.0


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


class UrgencyLevel(StrEnum):
    """How urgently a pending approval needs attention.

    Thresholds: ``critical`` < 1 hour, ``high`` < 4 hours,
    ``normal`` >= 4 hours, ``no_expiry`` when no TTL is set.
    """

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    NO_EXPIRY = "no_expiry"


class ApprovalResponse(ApprovalItem):
    """Approval item enriched with computed urgency fields.

    Attributes:
        seconds_remaining: Seconds until expiry, clamped to 0.0 for
            expired items (``None`` if no TTL).
        urgency_level: Urgency classification based on time remaining.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    seconds_remaining: float | None = Field(
        ge=0.0,
        description="Seconds until expiry (null if no TTL set)",
    )
    urgency_level: UrgencyLevel = Field(
        description="Urgency classification based on remaining time",
    )


def _to_approval_response(
    item: ApprovalItem,
    *,
    now: datetime,
    urgency_critical_seconds: float,
    urgency_high_seconds: float,
) -> ApprovalResponse:
    """Convert an ApprovalItem to an ApprovalResponse with urgency fields.

    Args:
        item: The domain-layer approval item.
        now: Reference timestamp for computing seconds remaining.
        urgency_critical_seconds: Threshold below which urgency is
            ``CRITICAL`` (resolved per-request from the settings
            backend; falls back to the registry default).
        urgency_high_seconds: Threshold below which urgency is
            ``HIGH``.  Operators must satisfy
            ``urgency_critical_seconds < urgency_high_seconds``; the
            startup invariant validator
            (``lifecycle_helpers._validate_approval_urgency_invariant``)
            blocks bad combinations before traffic arrives.

    Returns:
        Response DTO with computed ``seconds_remaining`` and ``urgency_level``.
    """
    if item.expires_at is None:
        seconds_remaining = None
        urgency = UrgencyLevel.NO_EXPIRY
    else:
        seconds_remaining = max(0.0, (item.expires_at - now).total_seconds())
        # Inclusive comparisons: the settings contract is "at or below"
        # so a TTL exactly at the configured threshold is included in
        # the corresponding bucket (CRITICAL or HIGH) rather than spilling
        # into the next-laxer bucket.
        if seconds_remaining <= urgency_critical_seconds:
            urgency = UrgencyLevel.CRITICAL
        elif seconds_remaining <= urgency_high_seconds:
            urgency = UrgencyLevel.HIGH
        else:
            urgency = UrgencyLevel.NORMAL
    return ApprovalResponse(
        **item.model_dump(),
        seconds_remaining=seconds_remaining,
        urgency_level=urgency,
    )


async def _get_approval_or_404(
    app_state: AppState,
    approval_id: str,
) -> ApprovalItem:
    """Fetch an approval item or raise NotFoundError.

    Args:
        app_state: Application state containing the approval store.
        approval_id: Approval identifier.

    Returns:
        The matching approval item.

    Raises:
        NotFoundError: If the approval is not found.
    """
    store = require_service(app_state.slice(ApprovalStateSlice).store, "Approval Store")
    return require_resource_or_404(
        await store.get(approval_id),
        resource_type="Approval",
        identifier=approval_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation="read",
    )
