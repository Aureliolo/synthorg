"""Tests for the health monitoring pipeline (end-to-end)."""

import pytest

from synthorg.engine.health.judge import HealthJudge
from synthorg.engine.health.pipeline import HealthMonitoringPipeline
from synthorg.engine.health.triage import TriageFilter
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.quality.models import StepQuality, StepQualitySignal
from synthorg.notifications.models import Notification, NotificationCategory


class _FakeSink:
    """In-memory notification sink for testing."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    @property
    def sink_name(self) -> str:
        return "fake"

    async def send(self, notification: Notification) -> None:
        self.sent.append(notification)

    async def start(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""

    async def close(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""


class _FailingSink:
    """Sink that raises on send."""

    @property
    def sink_name(self) -> str:
        return "failing"

    async def send(self, notification: Notification) -> None:
        msg = "Sink delivery failed"
        raise RuntimeError(msg)

    async def start(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""

    async def close(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""


def _signal(quality: StepQuality, step_index: int = 0) -> StepQualitySignal:
    return StepQualitySignal(
        quality=quality,
        confidence=0.7,
        reason="test",
        step_index=step_index,
        turn_range=(1, 1),
    )


@pytest.mark.unit
class TestHealthMonitoringPipeline:
    """End-to-end pipeline tests."""

    @pytest.fixture
    def sink(self) -> _FakeSink:
        return _FakeSink()

    @pytest.fixture
    def pipeline(self, sink: _FakeSink) -> HealthMonitoringPipeline:
        return HealthMonitoringPipeline(
            judge=HealthJudge(),
            triage=TriageFilter(),
            notification_sink=sink,
        )

    async def test_stagnation_escalated_and_notified(
        self,
        pipeline: HealthMonitoringPipeline,
        sink: _FakeSink,
    ) -> None:
        ticket = await pipeline.process(
            termination_reason=TerminationReason.STAGNATION,
            agent_id="agent-1",
            task_id="task-1",
            execution_duration=120.0,
        )
        assert ticket is not None
        assert len(sink.sent) == 1
        assert "stagnation" in sink.sent[0].title.lower()

    async def test_completed_no_notification(
        self,
        pipeline: HealthMonitoringPipeline,
        sink: _FakeSink,
    ) -> None:
        ticket = await pipeline.process(
            termination_reason=TerminationReason.COMPLETED,
            agent_id="agent-1",
            task_id="task-1",
        )
        assert ticket is None
        assert len(sink.sent) == 0

    async def test_error_with_recovery_medium_short_stall_dismissed(
        self,
        pipeline: HealthMonitoringPipeline,
        sink: _FakeSink,
    ) -> None:
        """MEDIUM ticket with short stall is dismissed by triage."""
        ticket = await pipeline.process(
            termination_reason=TerminationReason.ERROR,
            has_recovery=True,
            agent_id="agent-1",
            task_id="task-1",
            execution_duration=10.0,
        )
        # Judge emits MEDIUM, triage dismisses (short stall).
        assert ticket is None
        assert len(sink.sent) == 0

    async def test_error_with_recovery_long_stall_escalated(
        self,
        pipeline: HealthMonitoringPipeline,
        sink: _FakeSink,
    ) -> None:
        """MEDIUM ticket with long stall is escalated."""
        ticket = await pipeline.process(
            termination_reason=TerminationReason.ERROR,
            has_recovery=True,
            agent_id="agent-1",
            task_id="task-1",
            execution_duration=120.0,
        )
        assert ticket is not None
        assert len(sink.sent) == 1

    async def test_quality_degradation_escalated(
        self,
        pipeline: HealthMonitoringPipeline,
        sink: _FakeSink,
    ) -> None:
        signals = tuple(_signal(StepQuality.INCORRECT, i) for i in range(3))
        ticket = await pipeline.process(
            termination_reason=TerminationReason.COMPLETED,
            quality_signals=signals,
            agent_id="agent-1",
            task_id="task-1",
        )
        assert ticket is not None
        assert len(sink.sent) == 1

    async def test_stagnation_uses_health_category(
        self,
        pipeline: HealthMonitoringPipeline,
        sink: _FakeSink,
    ) -> None:
        await pipeline.process(
            termination_reason=TerminationReason.STAGNATION,
            agent_id="agent-1",
            task_id="task-1",
        )
        assert sink.sent[0].category == NotificationCategory.HEALTH

    async def test_notification_metadata_contains_ticket_info(
        self,
        pipeline: HealthMonitoringPipeline,
        sink: _FakeSink,
    ) -> None:
        await pipeline.process(
            termination_reason=TerminationReason.STAGNATION,
            agent_id="agent-1",
            task_id="task-1",
        )
        notification = sink.sent[0]
        assert notification.metadata["agent_id"] == "agent-1"
        assert notification.metadata["task_id"] == "task-1"
        assert "ticket_id" in notification.metadata

    async def test_sink_error_preserves_ticket(self) -> None:
        """Notification failure is best-effort -- ticket still returned."""
        pipeline = HealthMonitoringPipeline(
            judge=HealthJudge(),
            triage=TriageFilter(),
            notification_sink=_FailingSink(),
        )
        # Should not raise.
        ticket = await pipeline.process(
            termination_reason=TerminationReason.STAGNATION,
            agent_id="agent-1",
            task_id="task-1",
        )
        # Ticket is preserved even when notification delivery fails.
        assert ticket is not None
        assert ticket.agent_id == "agent-1"
