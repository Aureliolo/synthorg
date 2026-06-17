# module-kind: code
"""Configuration for the periodic model-refresh/reconcile subsystem.

``RefreshMode`` is the config discriminator selecting the refresh
strategy; ``ModelRefreshConfig`` is the frozen runtime view assembled
from the registered ``providers.model_refresh_*`` settings.  Field
defaults mirror those registered settings so a default-constructed
config (tests, no settings service) matches the off-by-cadence safe
default the operator sees.
"""

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_REFRESH_MODE_RESOLVE_FAILED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_DEFAULT_REFRESH_INTERVAL_SECONDS: Final[float] = 86_400.0
_MIN_REFRESH_INTERVAL_SECONDS: Final[float] = 60.0
_MAX_REFRESH_INTERVAL_SECONDS: Final[float] = 604_800.0

_REFRESH_NAMESPACE: Final[str] = "providers"
_MODE_KEY: Final[str] = "model_refresh_mode"
_INTERVAL_KEY: Final[str] = "model_refresh_interval_seconds"
_AUTO_APPLY_KEY: Final[str] = "model_refresh_auto_apply_within_family"


class RefreshMode(StrEnum):
    """How the periodic model-refresh subsystem operates.

    Attributes:
        OFF: Disabled entirely (the safe default); nothing is scheduled.
        MANUAL_ONLY: No cadence; only the explicit refresh endpoint runs.
        DETECT_ONLY: Periodically probe and flag removed models stale,
            but never persist new models or emit recommendations.
        RECONCILE_RECOMMEND: Probe, persist refreshed metadata, flag
            removed models stale, and feed upgrade recommendations.
    """

    OFF = "off"
    MANUAL_ONLY = "manual_only"
    DETECT_ONLY = "detect_only"
    RECONCILE_RECOMMEND = "reconcile_recommend"


# Authoritative enum-value tuple; the registered ``providers.model_refresh_mode``
# setting keeps the values as a literal to avoid a
# settings-definitions -> providers import cycle.
REFRESH_MODE_VALUES: Final[tuple[str, ...]] = tuple(m.value for m in RefreshMode)


class ModelRefreshConfig(BaseModel):
    """Runtime view of the model-refresh settings.

    Attributes:
        mode: The active refresh mode (config discriminator).
        interval_seconds: Cadence between automatic reconcile cycles.
        auto_apply_within_family: When set, strictly in-family upgrades
            are auto-applied instead of parked for human approval.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    mode: RefreshMode = RefreshMode.OFF
    interval_seconds: float = Field(
        default=_DEFAULT_REFRESH_INTERVAL_SECONDS,
        ge=_MIN_REFRESH_INTERVAL_SECONDS,
        le=_MAX_REFRESH_INTERVAL_SECONDS,
    )
    auto_apply_within_family: bool = False


async def resolve_refresh_mode(resolver: ConfigResolver) -> RefreshMode:
    """Resolve the live refresh mode, failing safe to ``OFF``.

    The scheduler reads this every tick so an operator can change mode
    without a restart.  Any resolution or coercion failure is treated as
    ``OFF`` so a settings-backend hiccup never silently runs a refresh.

    Returns:
        The resolved :class:`RefreshMode`, or ``RefreshMode.OFF`` on any
        read/coercion failure.
    """
    try:
        raw = await resolver.get_str(_REFRESH_NAMESPACE, _MODE_KEY)
        return RefreshMode(raw)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_MODEL_REFRESH_MODE_RESOLVE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return RefreshMode.OFF


async def _resolve_interval_seconds(resolver: ConfigResolver) -> float:
    """Resolve the cadence interval, failing safe to the default.

    Returns:
        The resolved interval, or :data:`_DEFAULT_REFRESH_INTERVAL_SECONDS`
        on any read/coercion failure.
    """
    try:
        return await resolver.get_float(_REFRESH_NAMESPACE, _INTERVAL_KEY)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_MODEL_REFRESH_MODE_RESOLVE_FAILED,
            key=_INTERVAL_KEY,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _DEFAULT_REFRESH_INTERVAL_SECONDS


async def load_model_refresh_config(resolver: ConfigResolver) -> ModelRefreshConfig:
    """Assemble a :class:`ModelRefreshConfig` from the registered settings.

    Each read fails safe to its field default (off mode, daily cadence,
    no auto-apply); an out-of-range stored interval is rejected by the
    field bounds and also falls back to the default, so a bad setting can
    never abort wiring or 500 the status endpoint.

    Returns:
        A frozen config view assembled from the registered settings.
    """
    mode = await resolve_refresh_mode(resolver)
    interval = await _resolve_interval_seconds(resolver)
    auto_apply = await resolve_bool_with_fallback(
        resolver=resolver,
        namespace=_REFRESH_NAMESPACE,
        key=_AUTO_APPLY_KEY,
        fallback=False,
    )
    try:
        return ModelRefreshConfig(
            mode=mode,
            interval_seconds=interval,
            auto_apply_within_family=auto_apply,
        )
    except ValidationError as exc:
        logger.warning(
            PROVIDER_MODEL_REFRESH_MODE_RESOLVE_FAILED,
            note="config_invalid",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ModelRefreshConfig(mode=mode, auto_apply_within_family=auto_apply)
