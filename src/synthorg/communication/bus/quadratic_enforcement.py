# module-kind: service
"""O(n^2) message-overhead enforcement for the message bus.

Detection mirrors ``MessageOverhead.is_quadratic``: over a sliding
window, the inter-agent publish count is compared to
``team_size^2 * quadratic_threshold``.  When a window is quadratic the
configured :class:`~synthorg.communication.enums.QuadraticEnforcementStrategy`
decides the response -- emit an alert, apply publish backpressure, or
reject new agent connections.

The enforcer is deliberately decoupled from the notification subsystem:
it always emits a structured observability event, and additionally
forwards to an optional :class:`QuadraticAlertSink` that boot wiring
late-binds to the real ``NotificationDispatcher`` (the bus is built in
the construction phase, before the dispatcher exists).
"""

from collections import deque
from typing import Protocol, runtime_checkable

from synthorg.communication.config import QuadraticEnforcementConfig
from synthorg.communication.enums import QuadraticEnforcementStrategy
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.communication import (
    COMM_QUADRATIC_CONNECTION_BLOCKED,
    COMM_QUADRATIC_DETECTED,
    COMM_QUADRATIC_THROTTLED,
)

logger = get_logger(__name__)


@runtime_checkable
class QuadraticAlertSink(Protocol):
    """Receives a human-readable alert when a quadratic window is detected."""

    async def alert(self, *, title: str, body: str) -> None:
        """Deliver an operator alert.

        Args:
            title: Short alert title.
            body: Human-readable alert body.
        """
        ...


class QuadraticEnforcer:
    """Applies the configured quadratic-overhead strategy on the bus.

    A single instance is held by a message-bus backend.  Hot-path calls
    are :meth:`on_publish` (per published message, after the bus lock is
    released) and :meth:`admit_agent` (before a new participant joins).

    Args:
        config: Enforcement settings.
        clock: Monotonic/now clock seam; defaults to ``SystemClock``.
    """

    __slots__ = ("_alert_sink", "_clock", "_config", "_last_alert_monotonic", "_window")

    def __init__(
        self,
        *,
        config: QuadraticEnforcementConfig,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._window: deque[float] = deque()
        self._alert_sink: QuadraticAlertSink | None = None
        # Sentinel below any real monotonic reading so the first
        # detection always alerts.
        self._last_alert_monotonic: float | None = None

    @property
    def strategy(self) -> QuadraticEnforcementStrategy:
        """The active enforcement strategy."""
        return self._config.strategy

    def set_alert_sink(self, sink: QuadraticAlertSink | None) -> None:
        """Late-bind the alert sink once the dispatcher is available.

        Args:
            sink: The alert sink, or ``None`` to clear it.
        """
        self._alert_sink = sink

    def admit_agent(self, *, current_agent_count: int) -> bool:
        """Whether another agent may join the bus.

        Under ``hard_block`` a new connection is rejected once the live
        participant count is at or above ``max_agent_connections``.
        Every other strategy admits unconditionally.

        Args:
            current_agent_count: Number of agents already participating.

        Returns:
            ``True`` when the new agent may join, ``False`` to reject.
        """
        if self._config.strategy != QuadraticEnforcementStrategy.HARD_BLOCK:
            return True
        if current_agent_count < self._config.max_agent_connections:
            return True
        logger.warning(
            COMM_QUADRATIC_CONNECTION_BLOCKED,
            current_agent_count=current_agent_count,
            max_agent_connections=self._config.max_agent_connections,
        )
        return False

    async def on_publish(self, *, team_size: int) -> None:
        """Record a publish and enforce the strategy for the current window.

        Must be called outside the bus lock: under ``soft_throttle`` it
        awaits a backpressure delay, which must not block other bus
        traffic.

        Args:
            team_size: Current number of participating agents.
        """
        if self._config.strategy == QuadraticEnforcementStrategy.DISABLED:
            return
        count = self._record_and_count()
        if not self._is_quadratic(team_size=team_size, count=count):
            return
        await self._alert(team_size=team_size, count=count)
        if self._config.strategy == QuadraticEnforcementStrategy.SOFT_THROTTLE:
            delay = self._config.throttle_delay_seconds
            if delay > 0:
                logger.warning(
                    COMM_QUADRATIC_THROTTLED,
                    team_size=team_size,
                    window_count=count,
                    delay_seconds=delay,
                )
                await self._clock.sleep(delay)

    def _record_and_count(self) -> int:
        """Append the current timestamp, prune the window, return the count.

        Returns:
            Number of publishes within the sliding window.
        """
        now = self._clock.monotonic()
        cutoff = now - self._config.window_seconds
        window = self._window
        window.append(now)
        # Keep events at exactly the window boundary (timestamp == cutoff,
        # i.e. window_seconds old): the window is the closed interval
        # [cutoff, now] so a burst landing on the boundary is not
        # undercounted by one.
        while window and window[0] < cutoff:
            window.popleft()
        return len(window)

    def _is_quadratic(self, *, team_size: int, count: int) -> bool:
        """Whether the windowed count exceeds the quadratic ceiling.

        Returns:
            ``True`` when ``team_size`` is large enough and the windowed
            publish count exceeds ``team_size^2 * quadratic_threshold``.
        """
        if team_size < self._config.min_team_size:
            return False
        ceiling = team_size * team_size * self._config.quadratic_threshold
        return count > ceiling

    async def _alert(self, *, team_size: int, count: int) -> None:
        """Emit the detection event and forward to the alert sink.

        Alerts are rate-limited to one per ``window_seconds`` so a
        sustained quadratic burst does not flood the log or the
        notification channel.  A failing sink is swallowed (non-critical)
        so a broken notification channel never breaks message delivery.
        """
        now = self._clock.monotonic()
        last = self._last_alert_monotonic
        if last is not None and (now - last) < self._config.window_seconds:
            return
        self._last_alert_monotonic = now
        logger.warning(
            COMM_QUADRATIC_DETECTED,
            team_size=team_size,
            window_count=count,
            strategy=self._config.strategy.value,
            window_seconds=self._config.window_seconds,
        )
        sink = self._alert_sink
        if sink is None:
            return
        title = "Quadratic communication overhead detected"
        body = (
            f"{count} inter-agent messages in {self._config.window_seconds:.0f}s "
            f"for a team of {team_size} exceeds the O(n^2) threshold "
            f"(strategy={self._config.strategy.value})"
        )
        try:
            await sink.alert(title=title, body=body)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                COMM_QUADRATIC_DETECTED,
                note="alert sink failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
