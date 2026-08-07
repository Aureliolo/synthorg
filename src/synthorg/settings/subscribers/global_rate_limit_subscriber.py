"""Global rate-limit tier settings subscriber.

Rebuilds ``RateLimitConfig`` from current values and swaps it into
``AppState`` so the middleware's next request sees the new caps. The
stores are untouched, so windows already in flight keep counting.
"""

from collections.abc import Sequence
from typing import cast

from synthorg.api.state import AppState
from synthorg.config.rate_limits import (
    KNOWN_WINDOWS,
    LiveRateLimits,
    RateLimitWindowUnit,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)

_NAMESPACE = "api"
# ``rate_limit_exclude_paths`` is deliberately absent: Litestar applies
# exclusions when the middleware is mounted, never per request, so no swap
# can move them. Watching it would rebuild the tiers and log that the limits
# were swapped, which is true of everything except the setting that changed.
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        (_NAMESPACE, "rate_limiter_enabled"),
        (_NAMESPACE, "rate_limit_floor_max_requests"),
        (_NAMESPACE, "rate_limit_unauth_max_requests"),
        (_NAMESPACE, "rate_limit_auth_max_requests"),
        (_NAMESPACE, "rate_limit_auth_endpoint_max_requests"),
        (_NAMESPACE, "rate_limit_time_unit"),
    }
)


class GlobalRateLimitSettingsSubscriber:
    """Swap the global tier config on any watched change.

    Args:
        app_state: Application state owning the live config.
        settings_service: Settings service the rebuild reads through.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "global-rate-limit-settings"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Rebuild the whole tier config and swap it in.

        Rebuilding every field rather than patching the changed one keeps
        the cross-tier invariant intact: the floor must stay at or above
        both user-gated caps, and validating a whole config is the only
        way to catch a change that breaks it. That also makes the batch one
        rebuild: every key in it is already re-read by the one below.

        Args:
            changes: The watched writes this rebuild carries.

        Raises:
            Exception: Re-raised after logging so the dispatcher records
                the failure with subscriber context. The previous config
                stays in place, so the limiter keeps enforcing.
        """
        try:
            rebuilt = LiveRateLimits(
                enabled=await self._read_bool("rate_limiter_enabled"),
                floor_max_requests=await self._read_int(
                    "rate_limit_floor_max_requests"
                ),
                unauth_max_requests=await self._read_int(
                    "rate_limit_unauth_max_requests"
                ),
                auth_max_requests=await self._read_int("rate_limit_auth_max_requests"),
                auth_endpoint_max_requests=await self._read_int(
                    "rate_limit_auth_endpoint_max_requests"
                ),
                time_unit=await self._read_unit(),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="global_rate_limit_config",
                trigger_key=describe_changes(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        self._app_state.per_op_limits.swap_global_config(rebuilt)
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            trigger_key=describe_changes(changes),
            note="global rate-limit tiers rebuilt and swapped",
        )

    async def _read_int(self, key: str) -> int:
        """Read an integer setting.

        Returns:
            The parsed value.

        Raises:
            ValueError: When the stored value is not an integer, so the
                caller keeps the previous config rather than enforcing a
                cap nobody configured.
        """
        result = await self._settings_service.get(_NAMESPACE, key)
        raw = str(result.value) if result.value is not None else ""
        if not raw.lstrip("+-").isdigit():
            msg = f"setting api.{key}={raw!r} is not an integer"
            raise ValueError(msg)
        return int(raw)

    async def _read_bool(self, key: str) -> bool:
        """Read a boolean setting.

        Returns:
            The parsed value.

        Raises:
            ValueError: When the value is neither ``true`` nor ``false``.
                Treating a typo as ``False`` would silently switch the
                limiter off, which is the one outcome worth refusing.
        """
        result = await self._settings_service.get(_NAMESPACE, key)
        raw = str(result.value) if result.value is not None else ""
        normalised = normalize_ascii_lowercase(raw)
        if normalised in {"true", "false"}:
            return normalised == "true"
        msg = f"setting api.{key}={raw!r} is not a valid boolean"
        raise ValueError(msg)

    async def _read_unit(self) -> RateLimitWindowUnit:
        """Read the window unit.

        Returns:
            The configured unit.

        Raises:
            ValueError: When the stored value names no known window. Keeping
                the previous config beats silently counting over a window
                nobody asked for.
        """
        result = await self._settings_service.get(_NAMESPACE, "rate_limit_time_unit")
        raw = normalize_ascii_lowercase(
            str(result.value) if result.value is not None else "minute"
        )
        if raw not in KNOWN_WINDOWS:
            msg = f"setting api.rate_limit_time_unit={raw!r} is not a known window"
            raise ValueError(msg)
        return cast("RateLimitWindowUnit", raw)
