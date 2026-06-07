"""Conformance tests for HR repository implementations.

Tests run against both SQLite and Postgres backends via the ``backend``
parametrized fixture. Covers LifecycleEventRepository, TaskMetricRepository,
and CollaborationMetricRepository.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import Complexity, TaskType
from synthorg.core.persistence_errors import QueryError
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import sid
from tests.unit.persistence.conftest import make_task


@pytest.mark.integration
class TestLifecycleEventRepository:
    """Conformance tests for LifecycleEventRepository."""

    async def test_save_and_list_lifecycle_events(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Save and list lifecycle events."""
        repo = backend.lifecycle_events
        now = datetime.now(UTC)
        event = AgentLifecycleEvent(
            id="evt-001",
            agent_id="agent-001",
            agent_name="TestAgent",
            event_type=LifecycleEventType.HIRED,
            timestamp=now,
            initiated_by="admin",
            details="Agent hired",
            metadata={"key": "value"},
        )

        await repo.save(event)
        events = await repo.list_events()

        assert len(events) >= 1
        saved = events[0]
        assert saved.id == "evt-001"
        assert saved.agent_id == "agent-001"
        assert saved.event_type == LifecycleEventType.HIRED

    async def test_list_events_by_agent_id(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """List events filtered by agent ID."""
        repo = backend.lifecycle_events
        now = datetime.now(UTC)
        event1 = AgentLifecycleEvent(
            id="evt-agent1",
            agent_id="agent-a",
            agent_name="AgentA",
            event_type=LifecycleEventType.HIRED,
            timestamp=now,
            initiated_by="admin",
            details="",
            metadata={},
        )
        event2 = AgentLifecycleEvent(
            id="evt-agent2",
            agent_id="agent-b",
            agent_name="AgentB",
            event_type=LifecycleEventType.FIRED,
            timestamp=now,
            initiated_by="admin",
            details="",
            metadata={},
        )

        await repo.save(event1)
        await repo.save(event2)

        a_events = await repo.list_events(agent_id="agent-a")
        assert len(a_events) >= 1
        assert all(e.agent_id == "agent-a" for e in a_events)

    async def test_list_events_by_type(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """List events filtered by event type."""
        repo = backend.lifecycle_events
        now = datetime.now(UTC)
        event = AgentLifecycleEvent(
            id="evt-type",
            agent_id="agent-001",
            agent_name="TestAgent",
            event_type=LifecycleEventType.HIRED,
            timestamp=now,
            initiated_by="admin",
            details="",
            metadata={},
        )

        await repo.save(event)
        hired = await repo.list_events(event_type=LifecycleEventType.HIRED)

        assert len(hired) >= 1
        assert all(e.event_type == LifecycleEventType.HIRED for e in hired)

    async def test_list_events_with_limit(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """List events respects limit parameter."""
        repo = backend.lifecycle_events
        now = datetime.now(UTC)
        for i in range(5):
            event = AgentLifecycleEvent(
                id=f"evt-{i}",
                agent_id="agent-001",
                agent_name="TestAgent",
                event_type=LifecycleEventType.HIRED,
                timestamp=now,
                initiated_by="admin",
                details="",
                metadata={},
            )
            await repo.save(event)

        limited = await repo.list_events(limit=2)
        assert len(limited) <= 2

    async def test_list_events_rejects_naive_since(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """A naive ``since`` is rejected so the cut-off cannot drift."""
        with pytest.raises(QueryError):
            await backend.lifecycle_events.list_events(
                since=datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )


@pytest.mark.integration
class TestTaskMetricRepository:
    """Conformance tests for TaskMetricRepository."""

    async def test_save_and_query_task_metrics(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Save and query task metrics."""
        from datetime import timedelta

        task_repo = backend.tasks
        metric_repo = backend.task_metrics
        now = datetime.now(UTC)
        started = now - timedelta(seconds=31)  # 31 seconds before now

        # Save parent task first
        task = make_task(task_id="task-001", task_type=TaskType.RESEARCH)
        await task_repo.save(task)

        metric = TaskMetricRecord(
            id="tm-001",
            agent_id="agent-001",
            task_id=sid("task-001"),
            task_type=TaskType.RESEARCH,
            started_at=started,
            completed_at=now,
            is_success=True,
            duration_seconds=30.5,
            cost=0.50,
            currency="EUR",
            turns_used=5,
            tokens_used=1000,
            quality_score=0.95,
            complexity=Complexity.COMPLEX,
        )

        await metric_repo.save(metric)
        records = await metric_repo.query()

        assert len(records) >= 1
        saved = records[0]
        assert saved.id == "tm-001"
        assert saved.agent_id == "agent-001"
        assert saved.is_success is True
        # Currency must round-trip through the repository.  A silent drop
        # or overwrite here would leave the persisted row in an
        # unrecoverable "numeric cost without a unit" state.
        assert saved.currency == "EUR"
        assert saved.cost == pytest.approx(0.50)

    async def test_query_metrics_by_agent(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Query metrics filtered by agent ID."""
        from datetime import timedelta

        task_repo = backend.tasks
        metric_repo = backend.task_metrics
        now = datetime.now(UTC)
        started = now - timedelta(seconds=31)

        # Save parent task first
        task = make_task(task_id="task-001", task_type=TaskType.RESEARCH)
        await task_repo.save(task)

        metric = TaskMetricRecord(
            id="tm-agent",
            agent_id="agent-x",
            task_id=sid("task-001"),
            task_type=TaskType.RESEARCH,
            started_at=started,
            completed_at=now,
            is_success=True,
            duration_seconds=30.0,
            cost=0.50,
            currency="EUR",
            turns_used=5,
            tokens_used=1000,
            quality_score=0.90,
            complexity=Complexity.MEDIUM,
        )

        await metric_repo.save(metric)
        records = await metric_repo.query(agent_id="agent-x")

        assert len(records) >= 1
        assert all(r.agent_id == "agent-x" for r in records)

    async def test_query_metrics_by_time_range(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Query metrics with time range filters."""
        from datetime import timedelta

        task_repo = backend.tasks
        metric_repo = backend.task_metrics
        now = datetime.now(UTC)
        started = now - timedelta(seconds=31)

        # Save parent task first
        task = make_task(task_id="task-001", task_type=TaskType.RESEARCH)
        await task_repo.save(task)

        metric = TaskMetricRecord(
            id="tm-time",
            agent_id="agent-001",
            task_id=sid("task-001"),
            task_type=TaskType.RESEARCH,
            started_at=started,
            completed_at=now,
            is_success=True,
            duration_seconds=30.0,
            cost=0.50,
            currency="EUR",
            turns_used=5,
            tokens_used=1000,
            quality_score=0.90,
            complexity=Complexity.SIMPLE,
        )

        await metric_repo.save(metric)
        # Query with since = now should exclude this metric if it's just barely earlier
        # So we use a time well before now
        past = now - timedelta(hours=1)
        records = await metric_repo.query(since=past)

        assert len(records) >= 1

    async def test_query_rejects_naive_bounds(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Both ``since`` and ``until`` must be timezone-aware."""
        naive = datetime(2025, 1, 1)  # noqa: DTZ001 -- naive on purpose
        with pytest.raises(QueryError):
            await backend.task_metrics.query(since=naive)
        with pytest.raises(QueryError):
            await backend.task_metrics.query(until=naive)


@pytest.mark.integration
class TestCollaborationMetricRepository:
    """Conformance tests for CollaborationMetricRepository."""

    async def test_save_and_query_collaboration_metrics(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Save and query collaboration metrics."""
        repo = backend.collaboration_metrics
        now = datetime.now(UTC)
        metric = CollaborationMetricRecord(
            id="cm-001",
            agent_id="agent-001",
            recorded_at=now,
            delegation_success=True,
            delegation_response_seconds=5.2,
            conflict_constructiveness=0.8,
            meeting_contribution=0.9,
            loop_triggered=False,
            handoff_completeness=0.95,
        )

        await repo.save(metric)
        records = await repo.query()

        assert len(records) >= 1
        saved = records[0]
        assert saved.id == "cm-001"
        assert saved.agent_id == "agent-001"
        assert saved.delegation_success is True
        assert saved.loop_triggered is False

    async def test_query_metrics_by_agent(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Query collaboration metrics filtered by agent ID."""
        repo = backend.collaboration_metrics
        now = datetime.now(UTC)
        metric = CollaborationMetricRecord(
            id="cm-agent",
            agent_id="agent-y",
            recorded_at=now,
            delegation_success=False,
            delegation_response_seconds=10.0,
            conflict_constructiveness=0.5,
            meeting_contribution=0.6,
            loop_triggered=True,
            handoff_completeness=0.7,
        )

        await repo.save(metric)
        records = await repo.query(agent_id="agent-y")

        assert len(records) >= 1
        assert all(r.agent_id == "agent-y" for r in records)

    async def test_query_metrics_with_nullable_fields(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Query metrics with nullable fields set to None."""
        repo = backend.collaboration_metrics
        now = datetime.now(UTC)
        metric = CollaborationMetricRecord(
            id="cm-nullable",
            agent_id="agent-001",
            recorded_at=now,
            delegation_success=None,
            delegation_response_seconds=None,
            conflict_constructiveness=None,
            meeting_contribution=None,
            loop_triggered=False,
            handoff_completeness=None,
        )

        await repo.save(metric)
        records = await repo.query(agent_id="agent-001")

        assert len(records) >= 1
        saved = next(r for r in records if r.id == "cm-nullable")
        assert saved.delegation_success is None
        assert saved.delegation_response_seconds is None
        assert saved.loop_triggered is False

    async def test_query_metrics_since_timestamp(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """Query metrics with since timestamp filter."""
        repo = backend.collaboration_metrics
        now = datetime.now(UTC)
        metric = CollaborationMetricRecord(
            id="cm-since",
            agent_id="agent-001",
            recorded_at=now,
            delegation_success=True,
            delegation_response_seconds=5.0,
            conflict_constructiveness=0.8,
            meeting_contribution=0.9,
            loop_triggered=False,
            handoff_completeness=0.95,
        )

        await repo.save(metric)
        from datetime import timedelta

        past = now - timedelta(hours=1)
        records = await repo.query(since=past)

        assert len(records) >= 1

    async def test_query_rejects_naive_since(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """A naive ``since`` is rejected so the cut-off cannot drift."""
        with pytest.raises(QueryError):
            await backend.collaboration_metrics.query(
                since=datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )
