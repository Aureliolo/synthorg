"""Webhook event bus bridge.

Subscribes to the ``#webhooks`` bus channel and forwards events
into ``ExternalTriggerStrategy.on_external_event()`` on active
sprints.
"""

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.message import DataPart
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.strategies.external_trigger import (
    ExternalTriggerStrategy,
)
from synthorg.integrations.webhooks.event_bus_bridge import WEBHOOK_CHANNEL
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.integrations import (
    WEBHOOK_BRIDGE_EVENT_FORWARDED,
    WEBHOOK_BRIDGE_PAUSED,
    WEBHOOK_BRIDGE_POLL_ERROR,
    WEBHOOK_BRIDGE_RESOLVE_FAILED,
    WEBHOOK_BRIDGE_STARTED,
    WEBHOOK_BRIDGE_STOPPED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_SUBSCRIBER_ID: Final[str] = "__webhook_bridge__"
_POLL_TIMEOUT: Final[float] = 1.0
"""Fallback poll timeout used when no resolver is wired in."""
_MAX_CONSECUTIVE_ERRORS: Final[int] = 30
"""Fallback error budget used when no resolver is wired in."""


class _WebhookEvent(BaseModel):  # lint-allow: frozen-extra-forbid -- bus metadata
    """Typed view of a ``#webhooks`` ``DataPart`` payload.

    The bus part carries the dispatching ``connection_name`` and other
    transport metadata alongside the event fields, so unrelated keys are
    ignored rather than rejected.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    event_type: NotBlankStr
    payload: Mapping[str, object] = Field(default_factory=dict)
    connection_name: str | None = None


class WebhookEventBridge:
    """Bridges webhook bus events to the ceremony scheduler.

    Subscribes to ``#webhooks`` and forwards each verified event
    into the active sprint's ``ExternalTriggerStrategy`` (if any).

    Args:
        bus: The message bus instance.
        ceremony_scheduler: The ceremony scheduler holding the
            active sprint and strategy.
    """

    def __init__(
        self,
        bus: MessageBus,
        ceremony_scheduler: CeremonyScheduler,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._bus = bus
        self._scheduler = ceremony_scheduler
        self._config_resolver = config_resolver
        self._task: asyncio.Task[None] | None = None
        # Eager lifecycle lock per ``docs/reference/lifecycle-sync.md``;
        # ``asyncio.Lock`` is loop-agnostic until first ``acquire()``,
        # so app-wire-time construction is safe and prevents a racing
        # ``stop()`` from observing a half-published lock attribute.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # Resolver-failure warnings are log-once per run of failures
        # to keep the polling loop from flooding logs during a
        # prolonged settings outage. Flags reset on the first
        # successful resolution so a later failure is visible again.
        self._poll_timeout_fallback_logged: bool = False
        self._max_errors_fallback_logged: bool = False
        self._enabled_fallback_logged: bool = False

    def set_config_resolver(self, resolver: ConfigResolver) -> None:
        """Inject the ConfigResolver after construction.

        ``WebhookEventBridge`` is instantiated before ``AppState`` in
        :func:`synthorg.api.app.create_app` (because ``AppState``
        takes it as a constructor argument), so the resolver is not
        available at construction time. The API startup hook calls
        this setter after ``AppState`` is built and before
        :meth:`start` so polling-loop reads of the operator-tuned
        poll timeout and error budget are honoured.
        """
        self._config_resolver = resolver

    async def _get_poll_timeout(self) -> float:
        """Resolve the current poll timeout, falling back to the constant.

        A transient settings outage or malformed value must not crash
        the polling loop. Warnings are log-once per run of failures
        (cleared on recovery) so a prolonged outage cannot flood logs.

        Returns:
            The operator-tuned poll timeout in seconds, or the
            ``_POLL_TIMEOUT`` fallback when no resolver is wired or
            the resolver fails.

        Raises:
            asyncio.CancelledError: If the polling task is cancelled
                during the resolver call.
        """
        if self._config_resolver is None:
            return _POLL_TIMEOUT
        try:
            value = await self._config_resolver.get_float(
                SettingNamespace.COMMUNICATION.value,
                "webhook_bridge_poll_timeout_seconds",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            if not self._poll_timeout_fallback_logged:
                logger.warning(
                    WEBHOOK_BRIDGE_POLL_ERROR,
                    error=(
                        "failed to resolve webhook_bridge_poll_timeout_seconds;"
                        " using fallback (logging suppressed until recovery)"
                    ),
                    poll_timeout=_POLL_TIMEOUT,
                )
                self._poll_timeout_fallback_logged = True
            return _POLL_TIMEOUT
        self._poll_timeout_fallback_logged = False
        return value

    async def _get_max_consecutive_errors(self) -> int:
        """Resolve the current error budget, falling back to the constant.

        Same guard and log-once-per-failure-run semantics as
        :meth:`_get_poll_timeout`.

        Returns:
            The operator-tuned consecutive-error budget, or the
            ``_MAX_CONSECUTIVE_ERRORS`` fallback when no resolver is
            wired or the resolver fails.

        Raises:
            asyncio.CancelledError: If the polling task is cancelled
                during the resolver call.
        """
        if self._config_resolver is None:
            return _MAX_CONSECUTIVE_ERRORS
        try:
            value = await self._config_resolver.get_int(
                SettingNamespace.COMMUNICATION.value,
                "webhook_bridge_max_consecutive_errors",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            if not self._max_errors_fallback_logged:
                logger.warning(
                    WEBHOOK_BRIDGE_POLL_ERROR,
                    error=(
                        "failed to resolve webhook_bridge_max_consecutive_errors;"
                        " using fallback (logging suppressed until recovery)"
                    ),
                    max_errors=_MAX_CONSECUTIVE_ERRORS,
                )
                self._max_errors_fallback_logged = True
            return _MAX_CONSECUTIVE_ERRORS
        self._max_errors_fallback_logged = False
        return value

    async def start(self) -> None:
        """Subscribe and start the polling task."""
        async with self._lifecycle_lock:
            if self._task is not None:
                return
            await self._bus.subscribe(
                WEBHOOK_CHANNEL.name,
                _SUBSCRIBER_ID,
            )
            self._task = asyncio.create_task(
                self._poll_loop(),
                name="webhook-event-bridge",
            )
            # Surface ``MemoryError`` / ``RecursionError`` raised inside
            # ``_poll_loop``: without a done-callback, system-class
            # exceptions stay buffered on the task object and only
            # bubble up if someone awaits it -- which never happens
            # for a long-lived poll loop. ``log_task_exceptions`` logs
            # at CRITICAL and forwards the exception to the event-loop
            # exception handler so the process actually fails loud.
            self._task.add_done_callback(
                log_task_exceptions(
                    logger,
                    WEBHOOK_BRIDGE_POLL_ERROR,
                    subscriber_id=_SUBSCRIBER_ID,
                    channel=WEBHOOK_CHANNEL.name,
                ),
            )
            logger.info(WEBHOOK_BRIDGE_STARTED)

    async def stop(self) -> None:
        """Cancel the polling task and unsubscribe.

        If ``unsubscribe`` fails, the task reference is left in
        place and the exception propagates so the caller knows
        the bridge is in a partial-stop state. Clearing ``_task``
        on a failed unsubscribe would let a later ``start()``
        register a duplicate subscriber id against a live ghost
        subscription.
        """
        async with self._lifecycle_lock:
            if self._task is None:
                return
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            try:
                await self._bus.unsubscribe(
                    WEBHOOK_CHANNEL.name,
                    _SUBSCRIBER_ID,
                )
            except Exception:
                logger.warning(
                    WEBHOOK_BRIDGE_STOPPED,
                    subscriber_id=_SUBSCRIBER_ID,
                    channel=WEBHOOK_CHANNEL.name,
                    error=(
                        "unsubscribe failed -- bridge remains in "
                        "partial-stop state; call stop() again after "
                        "the bus recovers"
                    ),
                )
                raise
            self._task = None
            logger.info(WEBHOOK_BRIDGE_STOPPED)

    async def _resolve_enabled(self) -> bool:
        """Resolve the webhook-bridge kill-switch, fail-safe to ``True``.

        Operators flip ``communication.webhook_bridge_enabled=false``
        to pause event forwarding mid-flight without tearing down the
        bridge task. Resolver outage returns ``True`` because silently
        pausing event forwarding on a settings hiccup would queue
        webhook events indefinitely.

        Returns:
            The current operator setting, or ``True`` when no resolver
            is wired or the resolver fails (fail-safe).

        Raises:
            asyncio.CancelledError: If the polling task is cancelled
                during the resolver call.
        """
        if self._config_resolver is None:
            return True
        try:
            value = await self._config_resolver.get_bool(
                SettingNamespace.COMMUNICATION.value,
                "webhook_bridge_enabled",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            if not self._enabled_fallback_logged:
                logger.warning(
                    WEBHOOK_BRIDGE_RESOLVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    fallback_enabled=True,
                )
                self._enabled_fallback_logged = True
            return True
        self._enabled_fallback_logged = False
        return value

    async def _poll_loop(self) -> None:
        """Poll ``#webhooks`` and forward events.

        Poll timeout and max-error budget are cached per iteration so
        the receive call and the error-budget check observe the same
        values even if the operator edits the setting mid-iteration.

        Gated by ``communication.webhook_bridge_enabled`` (live,
        per-iteration): when False the loop stays resident but each
        iteration short-circuits before consuming a bus message.

        Raises:
            asyncio.CancelledError: If the polling task is cancelled
                by ``stop()``; propagation lets the surrounding
                ``contextlib.suppress`` in ``stop()`` short-circuit
                cleanly.
        """
        consecutive_errors = 0
        while True:
            if not await self._resolve_enabled():
                # Operator-controlled pause must not consume the error
                # budget: clear any pre-pause error streak so the first
                # post-resume transient failure does not stop the
                # bridge unexpectedly.
                consecutive_errors = 0
                logger.debug(WEBHOOK_BRIDGE_PAUSED, reason="paused_by_setting")
                await asyncio.sleep(await self._get_poll_timeout())
                continue
            poll_timeout = await self._get_poll_timeout()
            max_errors = await self._get_max_consecutive_errors()
            try:
                envelope = await self._bus.receive(
                    WEBHOOK_CHANNEL.name,
                    _SUBSCRIBER_ID,
                    timeout=poll_timeout,
                )
                if envelope is None:
                    continue
                await self._forward(envelope.message)
                # Ack only after forwarding succeeds; a ``_forward``
                # exception falls through to the catch-all below and
                # leaves the JetStream message un-acked so redelivery
                # picks it back up.
                await envelope.ack()
                # Reset the error streak only AFTER both forward and
                # ack succeed; resetting pre-ack lets a repeating ack
                # failure quietly bypass ``max_errors`` and keep the
                # loop running forever against a dead channel.
                consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # ``reraise_critical`` propagates catastrophic
                # interpreter-level errors (``MemoryError`` /
                # ``RecursionError``) to the event-loop exception
                # handler via the ``add_done_callback(log_task_exceptions(...))``
                # registered in ``start()``; logging-and-continuing past
                # them would mask the failure for the lifetime of the
                # bridge.
                reraise_critical(exc)
                # ``logger.exception`` would attach a traceback whose
                # frame-locals can leak transport credentials (auth
                # headers in NATS connect URLs, callback secrets in
                # event payloads). ``logger.warning`` / ``logger.error``
                # with ``error_type`` + scrubbed ``error`` keeps the
                # ack-vs-forward distinction visible during incident
                # triage without the credential-leak surface.
                consecutive_errors += 1
                if consecutive_errors >= max_errors:
                    log_exception_redacted(
                        logger,
                        WEBHOOK_BRIDGE_POLL_ERROR,
                        exc,
                        consecutive_errors=consecutive_errors,
                        note="too many consecutive errors, stopping",
                    )
                    # Unsubscribe before clearing the task reference
                    # so a later ``start()`` can register a fresh
                    # subscription. If unsubscribe fails we leave
                    # ``_task`` set so the bridge stays in a
                    # partial-stop state -- a subsequent ``start()``
                    # will skip re-registration (the ``_task is not
                    # None`` guard) and the stale subscription has
                    # to be recovered externally before another run.
                    try:
                        await self._bus.unsubscribe(
                            WEBHOOK_CHANNEL.name,
                            _SUBSCRIBER_ID,
                        )
                    except Exception as unsub_exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(unsub_exc)
                        logger.warning(
                            WEBHOOK_BRIDGE_STOPPED,
                            subscriber_id=_SUBSCRIBER_ID,
                            channel=WEBHOOK_CHANNEL.name,
                            error_type=type(unsub_exc).__name__,
                            error=safe_error_description(unsub_exc),
                            note=(
                                "unsubscribe failed after max "
                                "consecutive errors; leaving bridge "
                                "in partial-stop state"
                            ),
                        )
                        return
                    self._task = None
                    return
                logger.warning(
                    WEBHOOK_BRIDGE_POLL_ERROR,
                    consecutive_errors=consecutive_errors,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                # Back off for one poll interval before retrying so the
                # loop does not tight-spin on a hot error path.
                await asyncio.sleep(poll_timeout)

    async def _forward(self, message: object) -> None:
        """Extract event data and call on_external_event."""
        from synthorg.communication.message import Message  # noqa: PLC0415

        if not isinstance(message, Message):
            return
        strategy, sprint = await self._scheduler.get_active_info()
        if strategy is None or sprint is None:
            logger.debug(
                WEBHOOK_BRIDGE_EVENT_FORWARDED,
                reason="no active sprint or strategy",
            )
            return
        if not isinstance(strategy, ExternalTriggerStrategy):
            logger.debug(
                WEBHOOK_BRIDGE_EVENT_FORWARDED,
                reason="active strategy is not ExternalTriggerStrategy",
            )
            return

        for part in message.parts:
            if not isinstance(part, DataPart):
                continue
            try:
                event = parse_typed("workflow.webhook", part.data, _WebhookEvent)
            except ValidationError:
                # Skip a structurally-malformed part but keep forwarding
                # the rest of the message; ``parse_typed`` already logged
                # the validation failure with the boundary context.
                continue
            await strategy.on_external_event(
                sprint,
                event.event_type,
                event.payload,
            )
            logger.debug(
                WEBHOOK_BRIDGE_EVENT_FORWARDED,
                event_type=event.event_type,
                connection_name=event.connection_name,
            )
