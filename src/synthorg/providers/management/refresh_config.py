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

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_MODEL_REFRESH_MODE_RESOLVE_FAILED,
)
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


# Authoritative enum-value tuple mirrored by the registered
# ``providers.model_refresh_mode`` setting (kept literal there to avoid a
# settings-definitions -> providers import cycle; a unit test asserts parity).
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
        )
        return RefreshMode.OFF


async def load_model_refresh_config(resolver: ConfigResolver) -> ModelRefreshConfig:
    """Assemble a :class:`ModelRefreshConfig` from the registered settings.

    Returns:
        A frozen config view; falls back to field defaults (off,
        daily cadence, no auto-apply) on any read failure.
    """
    mode = await resolve_refresh_mode(resolver)
    interval = await resolver.get_float(_REFRESH_NAMESPACE, _INTERVAL_KEY)
    auto_apply = await resolver.get_bool(_REFRESH_NAMESPACE, _AUTO_APPLY_KEY)
    return ModelRefreshConfig(
        mode=mode,
        interval_seconds=interval,
        auto_apply_within_family=auto_apply,
    )
