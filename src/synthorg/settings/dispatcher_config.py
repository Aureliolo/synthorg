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

Resolve failures are logged once per distinct error type (re-arming when the
failure mode changes or a resolve succeeds) so a sustained settings-backend
outage -- read on every poll iteration across several methods -- does not flood
the log, while a genuine change of failure mode is still surfaced.
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
        # The error type of the last logged resolve failure, or ``None`` when
        # the dedup is armed. Re-armed on a successful resolve and whenever the
        # failure type changes, so an outage logs once per failure mode rather
        # than once per poll iteration.
        self._last_logged_error_type: str | None = None

    def reset(self) -> None:
        """Re-arm the resolve-failure log dedup.

        Called from ``SettingsChangeDispatcher.start()`` so a fresh lifecycle
        does not inherit a stale dedup flag from a prior run that ended mid
        outage (which would silently drop the new lifecycle's first warning).
        """
        self._last_logged_error_type = None

    def _resolver(self) -> ConfigResolverProtocol | None:
        """Return the live config resolver, or ``None`` before it is wired.

        Reads through the late-bound getter on every call so a resolver
        composed onto the slice after the dispatcher started is picked up
        without reconstruction.
        """
        if self._config_resolver_getter is None:
            return None
        return self._config_resolver_getter()

    def _note_resolve_failure(
        self,
        exc: Exception,
        *,
        key: str | None = None,
        fallback: float | int | None = None,
    ) -> None:
        """Log a resolve failure once per distinct error type."""
        error_type = type(exc).__name__
        if error_type == self._last_logged_error_type:
            return
        self._last_logged_error_type = error_type
        fields: dict[str, object] = {
            "error_type": error_type,
            "error": safe_error_description(exc),
        }
        if key is not None:
            fields["key"] = key
        if fallback is not None:
            fields["fallback"] = fallback
        logger.warning(SETTINGS_DISPATCHER_RESOLVE_FAILED, **fields)

    async def enabled(self) -> bool:
        """Resolve the kill-switch flag, fail-safe to ``True``.

        Operators flip ``settings.dispatcher_enabled=false`` to pause
        the propagation loop without tearing down subscribers. A
        settings-backend outage must not silently silence the
        dispatcher (the operator is the only sanctioned silencer), so
        any resolver failure resolves to enabled.

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
            self._note_resolve_failure(exc, key="dispatcher_enabled")
            return True
        self._last_logged_error_type = None
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
            value = await resolver.get_int(
                SettingNamespace.SETTINGS.value, "dispatcher_max_consecutive_errors"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._note_resolve_failure(
                exc,
                key="dispatcher_max_consecutive_errors",
                fallback=_MAX_CONSECUTIVE_ERRORS,
            )
            return _MAX_CONSECUTIVE_ERRORS
        self._last_logged_error_type = None
        return value

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
            value = await resolver.get_float(
                SettingNamespace.SETTINGS.value,
                "dispatcher_stop_drain_timeout_seconds",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._note_resolve_failure(
                exc,
                key="dispatcher_stop_drain_timeout_seconds",
                fallback=_STOP_DRAIN_TIMEOUT,
            )
            return _STOP_DRAIN_TIMEOUT
        self._last_logged_error_type = None
        return value

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
            value = await resolver.get_float(
                SettingNamespace.SETTINGS.value, "dispatcher_poll_timeout_seconds"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._note_resolve_failure(
                exc, key="dispatcher_poll_timeout_seconds", fallback=_POLL_TIMEOUT
            )
            return _POLL_TIMEOUT
        self._last_logged_error_type = None
        return value

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
            value = await resolver.get_float(
                SettingNamespace.SETTINGS.value, "dispatcher_error_backoff_seconds"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._note_resolve_failure(
                exc, key="dispatcher_error_backoff_seconds", fallback=_ERROR_BACKOFF
            )
            return _ERROR_BACKOFF
        self._last_logged_error_type = None
        return value


__all__ = ["DispatcherConfigReader"]
