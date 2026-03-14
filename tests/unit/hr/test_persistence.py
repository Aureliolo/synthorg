"""Tests for HR SQLite repository implementations."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
import pytest

from synthorg.core.enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)
from synthorg.persistence.sqlite.hr_repositories import (
    SQLiteCollaborationMetricRepository,
    SQLiteLifecycleEventRepository,
    SQLiteTaskMetricRepository,
)
from synthorg.persistence.sqlite.migrations import run_migrations

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    """In-memory SQLite connection with all migrations applied."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await run_migrations(conn)
    yield conn
    await conn.close()


def _make_lifecycle_event(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    agent_name: str = "Alice",
    event_type: LifecycleEventType = LifecycleEventType.HIRED,
    initiated_by: str = "hr-manager",
    details: str = "Hired via hiring pipeline",
    timestamp: datetime | None = None,
    metadata: dict[str, str] | None = None,
) -> AgentLifecycleEvent:
    return AgentLifecycleEvent(
        agent_id=NotBlankStr(agent_id),
        agent_name=NotBlankStr(agent_name),
        event_type=event_type,
        timestamp=timestamp or datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        initiated_by=NotBlankStr(initiated_by),
        details=details,
        metadata=metadata or {},
    )


def _make_task_metric(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    task_type: TaskType = TaskType.DEVELOPMENT,
    is_success: bool = True,
    duration_seconds: float = 120.0,
    cost_usd: float = 0.05,
    turns_used: int = 10,
    tokens_used: int = 5000,
    quality_score: float | None = 8.5,
    complexity: Complexity = Complexity.MEDIUM,
    completed_at: datetime | None = None,
) -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(task_id),
        task_type=task_type,
        completed_at=completed_at or datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        is_success=is_success,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
        turns_used=turns_used,
        tokens_used=tokens_used,
        quality_score=quality_score,
        complexity=complexity,
    )


def _make_collab_metric(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    delegation_success: bool | None = True,
    delegation_response_seconds: float | None = 5.0,
    conflict_constructiveness: float | None = 0.8,
    meeting_contribution: float | None = 0.7,
    loop_triggered: bool = False,
    handoff_completeness: float | None = 0.9,
    recorded_at: datetime | None = None,
) -> CollaborationMetricRecord:
    return CollaborationMetricRecord(
        agent_id=NotBlankStr(agent_id),
        recorded_at=recorded_at or datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        delegation_success=delegation_success,
        delegation_response_seconds=delegation_response_seconds,
        conflict_constructiveness=conflict_constructiveness,
        meeting_contribution=meeting_contribution,
        loop_triggered=loop_triggered,
        handoff_completeness=handoff_completeness,
    )


# ── SQLiteLifecycleEventRepository ────────────────────────────────


@pytest.mark.unit
class TestSQLiteLifecycleEventRepository:
    async def test_save_and_list_all(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteLifecycleEventRepository(db)
        event = _make_lifecycle_event()
        await repo.save(event)

        events = await repo.list_events()
        assert len(events) == 1
        assert events[0].agent_id == "agent-001"
        assert events[0].event_type == LifecycleEventType.HIRED

    async def test_list_filter_by_agent_id(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteLifecycleEventRepository(db)
        await repo.save(_make_lifecycle_event(agent_id="agent-001"))
        await repo.save(_make_lifecycle_event(agent_id="agent-002"))

        events = await repo.list_events(agent_id="agent-001")
        assert len(events) == 1
        assert events[0].agent_id == "agent-001"

    async def test_list_filter_by_event_type(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteLifecycleEventRepository(db)
        await repo.save(_make_lifecycle_event(event_type=LifecycleEventType.HIRED))
        await repo.save(_make_lifecycle_event(event_type=LifecycleEventType.FIRED))

        events = await repo.list_events(event_type=LifecycleEventType.HIRED)
        assert len(events) == 1
        assert events[0].event_type == LifecycleEventType.HIRED

    async def test_list_combined_filters(self, db: aiosqlite.Connection) -> None:
        """agent_id + event_type filters combine with AND logic."""
        repo = SQLiteLifecycleEventRepository(db)
        await repo.save(
            _make_lifecycle_event(
                agent_id="agent-001",
                event_type=LifecycleEventType.HIRED,
            )
        )
        await repo.save(
            _make_lifecycle_event(
                agent_id="agent-001",
                event_type=LifecycleEventType.FIRED,
            )
        )
        await repo.save(
            _make_lifecycle_event(
                agent_id="agent-002",
                event_type=LifecycleEventType.HIRED,
            )
        )

        events = await repo.list_events(
            agent_id="agent-001",
            event_type=LifecycleEventType.HIRED,
        )
        assert len(events) == 1
        assert events[0].agent_id == "agent-001"
        assert events[0].event_type == LifecycleEventType.HIRED

    async def test_round_trip_metadata(self, db: aiosqlite.Connection) -> None:
        """Metadata dict survives JSON serialization round-trip."""
        repo = SQLiteLifecycleEventRepository(db)
        event = _make_lifecycle_event(
            metadata={"reason": "budget", "department": "engineering"}
        )
        await repo.save(event)

        events = await repo.list_events()
        assert events[0].metadata == {
            "reason": "budget",
            "department": "engineering",
        }

    async def test_list_empty(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteLifecycleEventRepository(db)
        events = await repo.list_events()
        assert events == ()

    async def test_list_filter_by_since(self, db: aiosqlite.Connection) -> None:
        """Events before 'since' are excluded."""
        repo = SQLiteLifecycleEventRepository(db)
        old_event = _make_lifecycle_event(
            agent_id="agent-001",
            timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        new_event = _make_lifecycle_event(
            agent_id="agent-001",
            timestamp=datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC),
        )
        await repo.save(old_event)
        await repo.save(new_event)

        cutoff = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
        events = await repo.list_events(since=cutoff)
        assert len(events) == 1
        assert events[0].timestamp >= cutoff


# ── SQLiteTaskMetricRepository ────────────────────────────────────


@pytest.mark.unit
class TestSQLiteTaskMetricRepository:
    async def test_save_and_query_all(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteTaskMetricRepository(db)
        record = _make_task_metric()
        await repo.save(record)

        records = await repo.query()
        assert len(records) == 1
        assert records[0].agent_id == "agent-001"
        assert records[0].is_success is True
        assert records[0].quality_score == 8.5
        assert records[0].complexity == Complexity.MEDIUM

    async def test_query_filter_by_agent_id(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteTaskMetricRepository(db)
        await repo.save(_make_task_metric(agent_id="agent-001"))
        await repo.save(_make_task_metric(agent_id="agent-002"))

        records = await repo.query(agent_id="agent-001")
        assert len(records) == 1
        assert records[0].agent_id == "agent-001"

    async def test_query_empty(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteTaskMetricRepository(db)
        records = await repo.query()
        assert records == ()

    async def test_round_trip_null_quality_score(
        self, db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteTaskMetricRepository(db)
        record = _make_task_metric(quality_score=None)
        await repo.save(record)

        records = await repo.query()
        assert records[0].quality_score is None

    async def test_round_trip_task_type_and_complexity(
        self, db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteTaskMetricRepository(db)
        record = _make_task_metric(
            task_type=TaskType.RESEARCH,
            complexity=Complexity.COMPLEX,
        )
        await repo.save(record)

        records = await repo.query()
        assert records[0].task_type == TaskType.RESEARCH
        assert records[0].complexity == Complexity.COMPLEX

    async def test_query_filter_by_since_and_until(
        self, db: aiosqlite.Connection
    ) -> None:
        """Records outside the since/until range are excluded."""
        repo = SQLiteTaskMetricRepository(db)
        early = _make_task_metric(
            task_id="task-early",
            completed_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        mid = _make_task_metric(
            task_id="task-mid",
            completed_at=datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC),
        )
        late = _make_task_metric(
            task_id="task-late",
            completed_at=datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC),
        )
        await repo.save(early)
        await repo.save(mid)
        await repo.save(late)

        since = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
        until = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
        records = await repo.query(since=since, until=until)
        assert len(records) == 1
        assert records[0].task_id == "task-mid"


# ── SQLiteCollaborationMetricRepository ───────────────────────────


@pytest.mark.unit
class TestSQLiteCollaborationMetricRepository:
    async def test_save_and_query_all(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteCollaborationMetricRepository(db)
        record = _make_collab_metric()
        await repo.save(record)

        records = await repo.query()
        assert len(records) == 1
        assert records[0].agent_id == "agent-001"
        assert records[0].delegation_success is True
        assert records[0].loop_triggered is False

    async def test_query_filter_by_agent_id(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteCollaborationMetricRepository(db)
        await repo.save(_make_collab_metric(agent_id="agent-001"))
        await repo.save(_make_collab_metric(agent_id="agent-002"))

        records = await repo.query(agent_id="agent-001")
        assert len(records) == 1
        assert records[0].agent_id == "agent-001"

    async def test_query_empty(self, db: aiosqlite.Connection) -> None:
        repo = SQLiteCollaborationMetricRepository(db)
        records = await repo.query()
        assert records == ()

    async def test_round_trip_nullable_fields(self, db: aiosqlite.Connection) -> None:
        """Nullable float/bool fields survive round-trip as None."""
        repo = SQLiteCollaborationMetricRepository(db)
        record = _make_collab_metric(
            delegation_success=None,
            delegation_response_seconds=None,
            conflict_constructiveness=None,
            meeting_contribution=None,
            handoff_completeness=None,
        )
        await repo.save(record)

        records = await repo.query()
        assert records[0].delegation_success is None
        assert records[0].delegation_response_seconds is None
        assert records[0].conflict_constructiveness is None
        assert records[0].meeting_contribution is None
        assert records[0].handoff_completeness is None

    async def test_round_trip_loop_triggered_true(
        self, db: aiosqlite.Connection
    ) -> None:
        """Boolean loop_triggered=True survives SQLite integer round-trip."""
        repo = SQLiteCollaborationMetricRepository(db)
        record = _make_collab_metric(loop_triggered=True)
        await repo.save(record)

        records = await repo.query()
        assert records[0].loop_triggered is True

    async def test_query_filter_by_since(self, db: aiosqlite.Connection) -> None:
        """Records before 'since' are excluded."""
        repo = SQLiteCollaborationMetricRepository(db)
        old_record = _make_collab_metric(
            recorded_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        new_record = _make_collab_metric(
            recorded_at=datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC),
        )
        await repo.save(old_record)
        await repo.save(new_record)

        cutoff = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
        records = await repo.query(since=cutoff)
        assert len(records) == 1
        assert records[0].recorded_at >= cutoff
