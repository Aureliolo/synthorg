"""Unit tests for O(n^2) message-overhead enforcement."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.bus.memory import InMemoryMessageBus
from synthorg.communication.bus.quadratic_enforcement import QuadraticEnforcer
from synthorg.communication.config import (
    MessageBusConfig,
    QuadraticEnforcementConfig,
)
from synthorg.communication.enums import (
    MessageType,
    QuadraticEnforcementStrategy,
)
from synthorg.communication.errors import QuadraticConnectionBlockedError
from synthorg.communication.message import Message, TextPart
from tests._shared import FakeClock


class _RecordingSink:
    """Quadratic alert sink that records every alert it receives."""

    def __init__(self) -> None:
        self.alerts: list[tuple[str, str]] = []

    async def alert(self, *, title: str, body: str) -> None:
        """Record the alert."""
        self.alerts.append((title, body))


def _config(  # noqa: PLR0913 -- keyword-only test config builder
    *,
    strategy: QuadraticEnforcementStrategy,
    max_agent_connections: int = 50,
    throttle_delay_seconds: float = 0.05,
    min_team_size: int = 3,
    quadratic_threshold: float = 0.5,
    window_seconds: float = 60.0,
) -> QuadraticEnforcementConfig:
    """Build an enforcement config with test-friendly defaults."""
    return QuadraticEnforcementConfig(
        strategy=strategy,
        max_agent_connections=max_agent_connections,
        throttle_delay_seconds=throttle_delay_seconds,
        min_team_size=min_team_size,
        quadratic_threshold=quadratic_threshold,
        window_seconds=window_seconds,
    )


async def _drive_publishes(
    enforcer: QuadraticEnforcer,
    *,
    team_size: int,
    count: int,
) -> None:
    """Drive ``count`` publishes through the enforcer for ``team_size``."""
    for _ in range(count):
        await enforcer.on_publish(team_size=team_size)


class TestQuadraticEnforcerDetection:
    """Detection + alerting behaviour across strategies."""

    @pytest.mark.unit
    async def test_disabled_never_alerts(self) -> None:
        sink = _RecordingSink()
        enforcer = QuadraticEnforcer(
            config=_config(strategy=QuadraticEnforcementStrategy.DISABLED),
            clock=FakeClock(),
        )
        enforcer.set_alert_sink(sink)
        await _drive_publishes(enforcer, team_size=3, count=20)
        assert sink.alerts == []

    @pytest.mark.unit
    async def test_alert_only_fires_when_quadratic(self) -> None:
        sink = _RecordingSink()
        enforcer = QuadraticEnforcer(
            config=_config(strategy=QuadraticEnforcementStrategy.ALERT_ONLY),
            clock=FakeClock(),
        )
        enforcer.set_alert_sink(sink)
        # team_size=3 -> ceiling = 9 * 0.5 = 4.5; the 5th publish crosses.
        await _drive_publishes(enforcer, team_size=3, count=5)
        assert len(sink.alerts) == 1
        assert "Quadratic" in sink.alerts[0][0]

    @pytest.mark.unit
    async def test_failing_sink_does_not_escape_on_publish(self) -> None:
        # A sink whose alert() raises must not break message delivery:
        # on_publish swallows the sink failure (best-effort alerting).
        class _RaisingSink:
            async def alert(self, *, title: str, body: str) -> None:
                del title, body
                msg = "sink down"
                raise RuntimeError(msg)

        enforcer = QuadraticEnforcer(
            config=_config(strategy=QuadraticEnforcementStrategy.ALERT_ONLY),
            clock=FakeClock(),
        )
        enforcer.set_alert_sink(_RaisingSink())
        # Drive past the quadratic ceiling; on_publish must not raise.
        await _drive_publishes(enforcer, team_size=3, count=6)

    @pytest.mark.unit
    async def test_below_min_team_size_never_detects(self) -> None:
        sink = _RecordingSink()
        enforcer = QuadraticEnforcer(
            config=_config(
                strategy=QuadraticEnforcementStrategy.ALERT_ONLY,
                min_team_size=4,
            ),
            clock=FakeClock(),
        )
        enforcer.set_alert_sink(sink)
        await _drive_publishes(enforcer, team_size=3, count=50)
        assert sink.alerts == []

    @pytest.mark.unit
    async def test_alert_rate_limited_to_once_per_window(self) -> None:
        sink = _RecordingSink()
        clock = FakeClock()
        enforcer = QuadraticEnforcer(
            config=_config(strategy=QuadraticEnforcementStrategy.ALERT_ONLY),
            clock=clock,
        )
        enforcer.set_alert_sink(sink)
        await _drive_publishes(enforcer, team_size=3, count=20)
        assert len(sink.alerts) == 1
        # Advancing past the window lets a fresh detection alert again.
        clock.advance(61.0)
        await _drive_publishes(enforcer, team_size=3, count=20)
        assert len(sink.alerts) == 2

    @pytest.mark.unit
    async def test_window_prunes_stale_publishes(self) -> None:
        sink = _RecordingSink()
        clock = FakeClock()
        enforcer = QuadraticEnforcer(
            config=_config(strategy=QuadraticEnforcementStrategy.ALERT_ONLY),
            clock=clock,
        )
        enforcer.set_alert_sink(sink)
        await _drive_publishes(enforcer, team_size=3, count=4)
        assert sink.alerts == []
        # After the window elapses the earlier publishes age out, so a
        # single fresh publish is well under the quadratic ceiling.
        clock.advance(61.0)
        await enforcer.on_publish(team_size=3)
        assert sink.alerts == []


class TestQuadraticEnforcerSoftThrottle:
    """soft_throttle applies backpressure via the clock seam."""

    @pytest.mark.unit
    async def test_soft_throttle_sleeps_when_quadratic(self) -> None:
        clock = FakeClock()
        enforcer = QuadraticEnforcer(
            config=_config(
                strategy=QuadraticEnforcementStrategy.SOFT_THROTTLE,
                throttle_delay_seconds=0.25,
            ),
            clock=clock,
        )
        await _drive_publishes(enforcer, team_size=3, count=5)
        assert clock.sleep_calls == (0.25,)

    @pytest.mark.unit
    async def test_soft_throttle_no_sleep_below_threshold(self) -> None:
        clock = FakeClock()
        enforcer = QuadraticEnforcer(
            config=_config(strategy=QuadraticEnforcementStrategy.SOFT_THROTTLE),
            clock=clock,
        )
        await _drive_publishes(enforcer, team_size=3, count=4)
        assert clock.sleep_calls == ()


class TestQuadraticEnforcerHardBlock:
    """hard_block rejects new agents past the participant ceiling."""

    @pytest.mark.unit
    def test_admit_blocks_at_ceiling(self) -> None:
        enforcer = QuadraticEnforcer(
            config=_config(
                strategy=QuadraticEnforcementStrategy.HARD_BLOCK,
                max_agent_connections=5,
            ),
        )
        assert enforcer.admit_agent(current_agent_count=4) is True
        assert enforcer.admit_agent(current_agent_count=5) is False
        assert enforcer.admit_agent(current_agent_count=9) is False

    @pytest.mark.unit
    def test_admit_always_true_for_other_strategies(self) -> None:
        for strategy in (
            QuadraticEnforcementStrategy.DISABLED,
            QuadraticEnforcementStrategy.ALERT_ONLY,
            QuadraticEnforcementStrategy.SOFT_THROTTLE,
        ):
            enforcer = QuadraticEnforcer(
                config=_config(strategy=strategy, max_agent_connections=1),
            )
            assert enforcer.admit_agent(current_agent_count=99) is True


def _bus_config(
    *,
    strategy: QuadraticEnforcementStrategy,
    max_agent_connections: int = 50,
    throttle_delay_seconds: float = 0.05,
    min_team_size: int = 3,
) -> MessageBusConfig:
    """Build a bus config carrying the enforcement strategy."""
    return MessageBusConfig(
        channels=("#general",),
        quadratic_enforcement=QuadraticEnforcementConfig(
            strategy=strategy,
            max_agent_connections=max_agent_connections,
            throttle_delay_seconds=throttle_delay_seconds,
            min_team_size=min_team_size,
        ),
    )


def _message(*, sender: str, to: str, channel: str = "#general") -> Message:
    """Build a test message."""
    return Message(
        timestamp=datetime.now(UTC),
        sender=sender,
        to=to,
        type=MessageType.TASK_UPDATE,
        channel=channel,
        parts=(TextPart(text="hi"),),
    )


class TestBusQuadraticIntegration:
    """End-to-end enforcement through the in-memory bus."""

    @pytest.mark.unit
    async def test_hard_block_rejects_new_subscriber(self) -> None:
        enforcer = QuadraticEnforcer(
            config=QuadraticEnforcementConfig(
                strategy=QuadraticEnforcementStrategy.HARD_BLOCK,
                max_agent_connections=2,
            ),
        )
        bus = InMemoryMessageBus(
            config=_bus_config(
                strategy=QuadraticEnforcementStrategy.HARD_BLOCK,
                max_agent_connections=2,
            ),
            quadratic_enforcer=enforcer,
        )
        await bus.start()
        try:
            await bus.subscribe("#general", "agent-1")
            await bus.subscribe("#general", "agent-2")
            with pytest.raises(QuadraticConnectionBlockedError):
                await bus.subscribe("#general", "agent-3")
            # An already-known agent is still re-admitted (idempotent).
            await bus.subscribe("#general", "agent-1")
        finally:
            await bus.stop()

    @pytest.mark.unit
    async def test_soft_throttle_backpressures_publish(self) -> None:
        clock = FakeClock()
        enforcer = QuadraticEnforcer(
            config=QuadraticEnforcementConfig(
                strategy=QuadraticEnforcementStrategy.SOFT_THROTTLE,
                throttle_delay_seconds=0.1,
                min_team_size=2,
            ),
            clock=clock,
        )
        bus = InMemoryMessageBus(
            config=_bus_config(
                strategy=QuadraticEnforcementStrategy.SOFT_THROTTLE,
                min_team_size=2,
            ),
            clock=clock,
            quadratic_enforcer=enforcer,
        )
        await bus.start()
        try:
            # Two direct participants -> team_size 2; ceiling = 4*0.5 = 2.
            for _ in range(4):
                await bus.send_direct(
                    _message(sender="agent-a", to="agent-b"),
                    recipient="agent-b",
                )
            assert any(call == 0.1 for call in clock.sleep_calls)
        finally:
            await bus.stop()
