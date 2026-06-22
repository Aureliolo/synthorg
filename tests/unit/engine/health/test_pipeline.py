"""Tests for the health monitoring pipeline (end-to-end)."""

import pytest

from synthorg.engine.health.judge import HealthJudge
from synthorg.engine.health.pipeline import HealthMonitoringPipeline
from synthorg.engine.health.triage import TriageFilter
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.quality.models import StepQuality, StepQualitySignal
from synthorg.notifications.models import Notification, NotificationCategory

pytestmark = pytest.mark.unit


class _FakeDispatcher:
    """In-memory notification dispatcher for testing."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def dispatch(self, notification: Notification) -> None:
        self.sent.append(notification)


class _FailingDispatcher:
    """Dispatcher that raises on dispatch."""

    async def dispatch(self, notification: Notification) -> None:
        msg = "Notification delivery failed"
        raise RuntimeError(msg)


def _signal(quality: StepQuality, step_index: int = 0) -> StepQualitySignal:
    return StepQualitySignal(
        quality=quality,
        confidence=0.7,
        reason="test",
        step_index=step_index,
        turn_range=(1, 1),
    )


class TestHealthMonitoringPipeline:
    """End-to-end pipeline tests."""

    @pytest.fixture
    def dispatcher(self) -> _FakeDispatcher:
        return _FakeDispatcher()

    @pytest.fixture
    def pipeline(self, dispatcher: _FakeDispatcher) -> HealthMonitoringPipeline:
        return HealthMonitoringPipeline(
            judge=HealthJudge(),
            triage=TriageFilter(),
            notification_dispatcher=dispatcher,
        )

    async def test_stagnation_escalated_and_notified(
        self,
        pipeline: HealthMonitoringPipeline,
        dispatcher: _FakeDispatcher,
    ) -> None:
        ticket = await pipeline.process(
            termination_reason=TerminationReason.STAGNATION,
            agent_id="agent-1",
            task_id="task-1",
            execution_duration=120.0,
        )
        assert ticket is not None
        assert len(dispatcher.sent) == 1
        assert "stagnation" in dispatcher.sent[0].title.lower()

    async def test_completed_no_notification(
        self,
        pipeline: HealthMonitoringPipeline,
        dispatcher: _FakeDispatcher,
    ) -> None:
        ticket = await pipeline.process(
            termination_reason=TerminationReason.COMPLETED,
            agent_id="agent-1",
            task_id="task-1",
        )
        assert ticket is None
        assert len(dispatcher.sent) == 0

    async def test_error_with_recovery_medium_short_stall_dismissed(
        self,
        pipeline: HealthMonitoringPipeline,
        dispatcher: _FakeDispatcher,
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
        assert len(dispatcher.sent) == 0

    async def test_error_with_recovery_long_stall_escalated(
        self,
        pipeline: HealthMonitoringPipeline,
        dispatcher: _FakeDispatcher,
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
        assert len(dispatcher.sent) == 1

    async def test_quality_degradation_escalated(
        self,
        pipeline: HealthMonitoringPipeline,
        dispatcher: _FakeDispatcher,
    ) -> None:
        signals = tuple(_signal(StepQuality.INCORRECT, i) for i in range(3))
        ticket = await pipeline.process(
            termination_reason=TerminationReason.COMPLETED,
            quality_signals=signals,
            agent_id="agent-1",
            task_id="task-1",
        )
        assert ticket is not None
        assert len(dispatcher.sent) == 1

    async def test_stagnation_uses_health_category(
        self,
        pipeline: HealthMonitoringPipeline,
        dispatcher: _FakeDispatcher,
    ) -> None:
        await pipeline.process(
            termination_reason=TerminationReason.STAGNATION,
            agent_id="agent-1",
            task_id="task-1",
        )
        assert dispatcher.sent[0].category == NotificationCategory.HEALTH

    async def test_notification_metadata_contains_ticket_info(
        self,
        pipeline: HealthMonitoringPipeline,
        dispatcher: _FakeDispatcher,
    ) -> None:
        await pipeline.process(
            termination_reason=TerminationReason.STAGNATION,
            agent_id="agent-1",
            task_id="task-1",
        )
        notification = dispatcher.sent[0]
        assert notification.metadata["agent_id"] == "agent-1"
        assert notification.metadata["task_id"] == "task-1"
        assert "ticket_id" in notification.metadata

    async def test_dispatch_error_preserves_ticket(self) -> None:
        """Notification failure is best-effort -- ticket still returned."""
        pipeline = HealthMonitoringPipeline(
            judge=HealthJudge(),
            triage=TriageFilter(),
            notification_dispatcher=_FailingDispatcher(),
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
