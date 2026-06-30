# module-kind: service
"""Late-bound config reader for :class:`SettingsChangeDispatcher`.

The dispatcher's own poll cadence, error backoff, consecutive-error budget,
stop-drain deadline, and operator kill switch are themselves DB-backed
settings. They are read through this reader on every loop iteration / stop so
an operator can retune them without a dispatcher restart. The resolver is
late-bound (composed onto its slice *after* the dispatcher is built and
started), so the reader holds a getter that re-reads the slice on each call
and fails safe to the bootstrap defaults until -- and whenever -- the resolver
is unavailable.
"""

import asyncio
from collections.abc import Callable
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_DISPATCHER_RESOLVE_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_POLL_TIMEOUT: Final[float] = 1.0
"""Bootstrap poll timeout used before the settings resolver is ready."""
_ERROR_BACKOFF: Final[float] = 1.0
"""Bootstrap error backoff used before the settings resolver is ready."""
_MAX_CONSECUTIVE_ERRORS: Final[int] = 30
"""Bootstrap consecutive-error budget used before the resolver is ready."""
_STOP_DRAIN_TIMEOUT: Final[float] = 10.0
"""Bootstrap stop() drain deadline used before the resolver is ready."""


class DispatcherConfigReader:
    """Resolve the dispatcher's own runtime knobs, fail-safe to bootstrap.

    Args:
        config_resolver_getter: Late-bound accessor returning the live
            :class:`ConfigResolverProtocol`, or ``None`` before the
            resolver is composed onto its slice (bootstrap / tests). A
            ``None`` getter, or a getter returning ``None``, resolves
            every knob to its bootstrap default.
    """

    def __init__(
        self,
        config_resolver_getter: Callable[[], ConfigResolverProtocol | None]
        | None = None,
    ) -> None:
        self._config_resolver_getter: (
            Callable[[], ConfigResolverProtocol | None] | None
        ) = config_resolver_getter
        self._resolve_failed_logged: bool = False

    def _resolver(self) -> ConfigResolverProtocol | None:
        """Return the live config resolver, or ``None`` before it is wired.

        Reads through the late-bound getter on every call so a resolver
        composed onto the slice after the dispatcher started is picked up
        without reconstruction.
        """
        if self._config_resolver_getter is None:
            return None
        return self._config_resolver_getter()

    async def enabled(self) -> bool:
        """Resolve the kill-switch flag, fail-safe to ``True``.

        Operators flip ``settings.dispatcher_enabled=false`` to pause
        the propagation loop without tearing down subscribers. A
        settings-backend outage must not silently silence the
        dispatcher (the operator is the only sanctioned silencer), so
        any resolver failure resolves to enabled. The first failure
        per run logs a WARNING; the surface re-arms on the next
        successful resolve so a transient outage does not fill the
        log with duplicates.

        Returns:
            ``True`` when the dispatcher should process messages
            (including every resolver-failure case, fail-safe), ``False``
            only when an operator has explicitly set
            ``settings.dispatcher_enabled=false``.

        Raises:
            asyncio.CancelledError: If the coroutine is cancelled while
                awaiting the resolver.
        """
        resolver = self._resolver()
        if resolver is None:
            return True
        try:
            value = await resolver.get_bool(
                SettingNamespace.SETTINGS.value, "dispatcher_enabled"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            if not self._resolve_failed_logged:
                logger.warning(
                    SETTINGS_DISPATCHER_RESOLVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                self._resolve_failed_logged = True
            return True
        self._resolve_failed_logged = False
        return value

    async def max_consecutive_errors(self) -> int:
        """Resolve the consecutive-error budget; bootstrap fallback is 30.

        Read each loop iteration so an operator can lower / raise the
        budget without a dispatcher restart. Resolver outage falls
        back to the bootstrap default so the loop keeps pumping with
        a sane default rather than aborting on the first error.

        The bootstrap literal duplicates the registered default in
        ``settings/definitions/settings.py``
        (``dispatcher_max_consecutive_errors``); kept inline as a
        literal because importing the registry value at module-load
        risks a circular import (registry depends on settings models;
        settings models depend on enums; the dispatcher module is on
        the resolution path back). Keep both in lockstep when
        adjusting the registered default.

        Returns:
            The configured maximum number of consecutive poll errors
            before the loop exits, or the bootstrap default of 30 when
            the resolver is unavailable.

        Raises:
            asyncio.CancelledError: If the coroutine is cancelled while
                awaiting the resolver.
        """
        resolver = self._resolver()
        if resolver is None:
            return _MAX_CONSECUTIVE_ERRORS
        try:
            return await resolver.get_int(
                SettingNamespace.SETTINGS.value, "dispatcher_max_consecutive_errors"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SETTINGS_DISPATCHER_RESOLVE_FAILED,
                key="dispatcher_max_consecutive_errors",
                fallback=_MAX_CONSECUTIVE_ERRORS,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _MAX_CONSECUTIVE_ERRORS

    async def stop_drain_timeout(self) -> float:
        """Resolve the stop() drain hard deadline; bootstrap fallback is 10.0s.

        Read once at stop() entry so an operator can extend the
        deadline ahead of a planned drain without code changes.
        Resolver outage falls back to the bootstrap default so the
        drain still bounds the lifecycle lock.

        The bootstrap literal duplicates the registered default in
        ``settings/definitions/settings.py``
        (``dispatcher_stop_drain_timeout_seconds``); kept inline for
        the same circular-import reason described on
        :meth:`max_consecutive_errors`. Keep both in lockstep when
        adjusting the registered default.

        Returns:
            The configured ``stop()`` drain timeout in seconds, or the
            bootstrap default of 10.0 when the resolver is unavailable.

        Raises:
            asyncio.CancelledError: If the coroutine is cancelled while
                awaiting the resolver.
        """
        resolver = self._resolver()
        if resolver is None:
            return _STOP_DRAIN_TIMEOUT
        try:
            return await resolver.get_float(
                SettingNamespace.SETTINGS.value,
                "dispatcher_stop_drain_timeout_seconds",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SETTINGS_DISPATCHER_RESOLVE_FAILED,
                key="dispatcher_stop_drain_timeout_seconds",
                fallback=_STOP_DRAIN_TIMEOUT,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _STOP_DRAIN_TIMEOUT

    async def poll_timeout(self) -> float:
        """Resolve the poll timeout; bootstrap fallback is ``_POLL_TIMEOUT``.

        Read each loop iteration so an operator can tune how fast the
        dispatcher reacts to changes (and how often it re-checks the kill
        switch) without a restart. Resolver outage falls back to the
        bootstrap default so the loop keeps yielding at a sane cadence.

        Returns:
            The configured poll timeout in seconds, or the bootstrap
            default when the resolver is unavailable.

        Raises:
            asyncio.CancelledError: If the coroutine is cancelled while
                awaiting the resolver.
        """
        resolver = self._resolver()
        if resolver is None:
            return _POLL_TIMEOUT
        try:
            return await resolver.get_float(
                SettingNamespace.SETTINGS.value, "dispatcher_poll_timeout_seconds"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SETTINGS_DISPATCHER_RESOLVE_FAILED,
                key="dispatcher_poll_timeout_seconds",
                fallback=_POLL_TIMEOUT,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _POLL_TIMEOUT

    async def error_backoff(self) -> float:
        """Resolve the post-error backoff; bootstrap fallback is ``_ERROR_BACKOFF``.

        Read on the error path so an operator can tune recovery cadence
        without a restart. Resolver outage falls back to the bootstrap
        default so the loop still backs off after a failed iteration.

        Returns:
            The configured error backoff in seconds, or the bootstrap
            default when the resolver is unavailable.

        Raises:
            asyncio.CancelledError: If the coroutine is cancelled while
                awaiting the resolver.
        """
        resolver = self._resolver()
        if resolver is None:
            return _ERROR_BACKOFF
        try:
            return await resolver.get_float(
                SettingNamespace.SETTINGS.value, "dispatcher_error_backoff_seconds"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SETTINGS_DISPATCHER_RESOLVE_FAILED,
                key="dispatcher_error_backoff_seconds",
                fallback=_ERROR_BACKOFF,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _ERROR_BACKOFF


__all__ = ["DispatcherConfigReader"]
