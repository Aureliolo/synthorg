"""Tests for SQLite repository implementations."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    import aiosqlite

from synthorg.budget.cost_record import CostRecord
from synthorg.communication.message import (
    Message,
    MessageMetadata,
)
from synthorg.core.enums import (
    ArtifactType,
    Complexity,
    CoordinationTopology,
    Priority,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.persistence.errors import DuplicateRecordError
from synthorg.persistence.sqlite.repositories import (
    SQLiteCostRecordRepository,
    SQLiteMessageRepository,
    SQLiteTaskRepository,
)
from tests.unit.persistence.conftest import make_message, make_task

# ── TaskRepository ───────────────────────────────────────────────


@pytest.mark.unit
class TestSQLiteTaskRepository:
    async def test_save_and_get(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        task = make_task()
        await repo.save(task)

        result = await repo.get("task-001")
        assert result is not None
        assert result.id == task.id
        assert result.title == task.title
        assert result.type == task.type
        assert result.status == TaskStatus.CREATED

    async def test_get_returns_none_for_missing(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        assert await repo.get("nonexistent") is None

    async def test_save_upsert_updates_existing(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        task = make_task()
        await repo.save(task)

        updated = task.with_transition(TaskStatus.ASSIGNED, assigned_to="bob")
        await repo.save(updated)

        result = await repo.get("task-001")
        assert result is not None
        assert result.status == TaskStatus.ASSIGNED
        assert result.assigned_to == "bob"

    async def test_list_all(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        await repo.save(make_task(task_id="t1"))
        await repo.save(make_task(task_id="t2"))

        tasks = await repo.list_tasks()
        assert len(tasks) == 2

    async def test_list_filter_by_status(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        await repo.save(make_task(task_id="t1"))
        t2 = make_task(task_id="t2").with_transition(
            TaskStatus.ASSIGNED, assigned_to="bob"
        )
        await repo.save(t2)

        created = await repo.list_tasks(status=TaskStatus.CREATED)
        assert len(created) == 1
        assert created[0].id == "t1"

    async def test_list_filter_by_assigned_to(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        t = make_task().with_transition(TaskStatus.ASSIGNED, assigned_to="bob")
        await repo.save(t)

        result = await repo.list_tasks(assigned_to="bob")
        assert len(result) == 1
        assert result[0].assigned_to == "bob"

    async def test_list_filter_by_project(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        await repo.save(make_task(task_id="t1", project="proj-a"))
        await repo.save(make_task(task_id="t2", project="proj-b"))

        result = await repo.list_tasks(project="proj-a")
        assert len(result) == 1
        assert result[0].project == "proj-a"

    async def test_delete_existing(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        await repo.save(make_task())
        assert await repo.delete("task-001") is True
        assert await repo.get("task-001") is None

    async def test_delete_nonexistent(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteTaskRepository(migrated_db)
        assert await repo.delete("nonexistent") is False

    async def test_list_with_combined_filters(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Multiple filters combine with AND logic."""
        repo = SQLiteTaskRepository(migrated_db)
        t1 = make_task(task_id="t1", project="proj-a")
        t2 = make_task(task_id="t2", project="proj-a").with_transition(
            TaskStatus.ASSIGNED, assigned_to="bob"
        )
        t3 = make_task(task_id="t3", project="proj-b").with_transition(
            TaskStatus.ASSIGNED, assigned_to="bob"
        )
        await repo.save(t1)
        await repo.save(t2)
        await repo.save(t3)

        result = await repo.list_tasks(status=TaskStatus.ASSIGNED, project="proj-a")
        assert len(result) == 1
        assert result[0].id == "t2"

    async def test_round_trip_with_nested_models(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Verify complex nested fields survive serialization."""
        from synthorg.core.artifact import ExpectedArtifact

        task = Task(
            id="task-complex",
            title="Complex task",
            description="Task with nested models",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="test-project",
            created_by="alice",
            estimated_complexity=Complexity.COMPLEX,
            budget_limit=50.0,
            deadline="2026-12-31T23:59:59",
            max_retries=3,
            task_structure=TaskStructure.PARALLEL,
            coordination_topology=CoordinationTopology.CENTRALIZED,
            reviewers=("reviewer-1", "reviewer-2"),
            dependencies=("dep-1", "dep-2"),
            artifacts_expected=(
                ExpectedArtifact(type=ArtifactType.CODE, path="src/main.py"),
                ExpectedArtifact(type=ArtifactType.TESTS, path="tests/"),
            ),
            acceptance_criteria=(
                AcceptanceCriterion(description="Tests pass"),
                AcceptanceCriterion(description="Code reviewed", met=True),
            ),
            delegation_chain=("manager", "lead"),
        )
        repo = SQLiteTaskRepository(migrated_db)
        await repo.save(task)

        result = await repo.get("task-complex")
        assert result is not None
        assert result.reviewers == ("reviewer-1", "reviewer-2")
        assert result.dependencies == ("dep-1", "dep-2")
        assert len(result.artifacts_expected) == 2
        assert result.artifacts_expected[0].type == ArtifactType.CODE
        assert result.artifacts_expected[0].path == "src/main.py"
        assert len(result.acceptance_criteria) == 2
        assert result.acceptance_criteria[1].met is True
        assert result.delegation_chain == ("manager", "lead")
        assert result.task_structure == TaskStructure.PARALLEL
        assert result.coordination_topology == CoordinationTopology.CENTRALIZED
        assert result.budget_limit == 50.0
        assert result.deadline == "2026-12-31T23:59:59"
        assert result.max_retries == 3


# ── CostRecordRepository ────────────────────────────────────────


@pytest.mark.unit
class TestSQLiteCostRecordRepository:
    def _make_record(
        self,
        *,
        agent_id: str = "alice",
        task_id: str = "task-001",
        cost_usd: float = 0.05,
    ) -> CostRecord:
        return CostRecord(
            agent_id=agent_id,
            task_id=task_id,
            provider="test-provider",
            model="test-model-001",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=cost_usd,
            timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )

    async def test_save_and_query(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        record = self._make_record()
        await repo.save(record)

        results = await repo.query()
        assert len(results) == 1
        assert results[0].agent_id == "alice"
        assert results[0].cost_usd == 0.05

    async def test_query_by_agent(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(self._make_record(agent_id="alice"))
        await repo.save(self._make_record(agent_id="bob"))

        results = await repo.query(agent_id="alice")
        assert len(results) == 1
        assert results[0].agent_id == "alice"

    async def test_query_by_task(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(self._make_record(task_id="t1"))
        await repo.save(self._make_record(task_id="t2"))

        results = await repo.query(task_id="t1")
        assert len(results) == 1
        assert results[0].task_id == "t1"

    async def test_aggregate_all(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(self._make_record(cost_usd=0.10))
        await repo.save(self._make_record(cost_usd=0.20))

        total = await repo.aggregate()
        assert abs(total - 0.30) < 1e-9

    async def test_aggregate_by_agent(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(self._make_record(agent_id="alice", cost_usd=0.10))
        await repo.save(self._make_record(agent_id="bob", cost_usd=0.20))

        total = await repo.aggregate(agent_id="alice")
        assert abs(total - 0.10) < 1e-9

    async def test_aggregate_by_task(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(self._make_record(task_id="t1", cost_usd=0.10))
        await repo.save(self._make_record(task_id="t2", cost_usd=0.20))

        total = await repo.aggregate(task_id="t1")
        assert abs(total - 0.10) < 1e-9

    async def test_aggregate_by_agent_and_task(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(
            self._make_record(agent_id="alice", task_id="t1", cost_usd=0.10)
        )
        await repo.save(
            self._make_record(agent_id="alice", task_id="t2", cost_usd=0.20)
        )
        await repo.save(self._make_record(agent_id="bob", task_id="t1", cost_usd=0.30))

        total = await repo.aggregate(agent_id="alice", task_id="t1")
        assert abs(total - 0.10) < 1e-9

    async def test_aggregate_empty(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        total = await repo.aggregate()
        assert total == 0.0

    async def test_query_with_combined_filters(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """agent_id + task_id filters combine correctly."""
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(self._make_record(agent_id="alice", task_id="t1"))
        await repo.save(self._make_record(agent_id="alice", task_id="t2"))
        await repo.save(self._make_record(agent_id="bob", task_id="t1"))

        results = await repo.query(agent_id="alice", task_id="t1")
        assert len(results) == 1
        assert results[0].agent_id == "alice"
        assert results[0].task_id == "t1"

    async def test_round_trip_with_call_category(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        from synthorg.budget.call_category import LLMCallCategory

        record = CostRecord(
            agent_id="alice",
            task_id="task-001",
            provider="test-provider",
            model="test-model-001",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.05,
            timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(record)

        results = await repo.query()
        assert results[0].call_category == LLMCallCategory.PRODUCTIVE

    async def test_round_trip_null_call_category(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCostRecordRepository(migrated_db)
        await repo.save(self._make_record())

        results = await repo.query()
        assert results[0].call_category is None


# ── MessageRepository ────────────────────────────────────────────


@pytest.mark.unit
class TestSQLiteMessageRepository:
    async def test_save_and_get_history(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteMessageRepository(migrated_db)
        msg = make_message()
        await repo.save(msg)

        history = await repo.get_history("general")
        assert len(history) == 1
        assert history[0].content == "Hello, world!"

    async def test_history_ordered_newest_first(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteMessageRepository(migrated_db)
        msg1 = make_message(
            timestamp=datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
            content="first",
        )
        msg2 = make_message(
            timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            content="second",
        )
        await repo.save(msg1)
        await repo.save(msg2)

        history = await repo.get_history("general")
        assert len(history) == 2
        assert history[0].content == "second"
        assert history[1].content == "first"

    async def test_history_with_limit(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteMessageRepository(migrated_db)
        for i in range(5):
            await repo.save(
                make_message(
                    timestamp=datetime(2026, 3, 1, i, 0, 0, tzinfo=UTC),
                    content=f"msg-{i}",
                )
            )

        history = await repo.get_history("general", limit=2)
        assert len(history) == 2

    async def test_history_filters_by_channel(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteMessageRepository(migrated_db)
        await repo.save(make_message(channel="general"))
        await repo.save(make_message(channel="engineering"))

        general = await repo.get_history("general")
        assert len(general) == 1
        assert general[0].channel == "general"

    async def test_duplicate_message_rejected(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteMessageRepository(migrated_db)
        fixed_id = uuid4()
        msg = make_message(msg_id=fixed_id)
        await repo.save(msg)

        with pytest.raises(DuplicateRecordError, match="already exists"):
            await repo.save(make_message(msg_id=fixed_id))

    async def test_round_trip_alias_from_field(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Verify the sender/'from' alias round-trips correctly."""
        repo = SQLiteMessageRepository(migrated_db)
        msg = make_message(sender="charlie")
        await repo.save(msg)

        history = await repo.get_history("general")
        assert history[0].sender == "charlie"

    async def test_round_trip_with_attachments_and_metadata(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Verify nested JSON fields round-trip correctly."""
        from synthorg.communication.enums import AttachmentType

        msg = make_message(
            metadata=MessageMetadata(
                task_id="task-001",
                project_id="proj-a",
                tokens_used=100,
                cost_usd=0.01,
                extra=(("key1", "val1"),),
            ),
        )
        # Add attachments via model_validate to bypass frozen
        msg_data = msg.model_dump(mode="json")
        msg_data["attachments"] = [
            {"type": AttachmentType.FILE, "ref": "/path/to/file"},
        ]
        msg_with_attach = Message.model_validate(msg_data)

        repo = SQLiteMessageRepository(migrated_db)
        await repo.save(msg_with_attach)

        history = await repo.get_history("general")
        result = history[0]
        assert len(result.attachments) == 1
        assert result.attachments[0].type == AttachmentType.FILE
        assert result.attachments[0].ref == "/path/to/file"
        assert result.metadata.task_id == "task-001"
        assert result.metadata.extra == (("key1", "val1"),)

    async def test_round_trip_uuid_id(self, migrated_db: aiosqlite.Connection) -> None:
        """Verify UUID id survives round-trip."""
        repo = SQLiteMessageRepository(migrated_db)
        msg = make_message()
        original_id = msg.id
        await repo.save(msg)

        history = await repo.get_history("general")
        assert history[0].id == original_id

    async def test_get_history_invalid_limit(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Negative or zero limit raises QueryError."""
        from synthorg.persistence.errors import QueryError

        repo = SQLiteMessageRepository(migrated_db)
        with pytest.raises(QueryError, match="positive integer"):
            await repo.get_history("general", limit=0)
        with pytest.raises(QueryError, match="positive integer"):
            await repo.get_history("general", limit=-1)


@pytest.mark.unit
class TestSQLiteRepoProtocolCompliance:
    """Verify SQLite repositories satisfy their protocol interfaces."""

    async def test_task_repo_implements_protocol(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.repositories import TaskRepository

        repo = SQLiteTaskRepository(migrated_db)
        assert isinstance(repo, TaskRepository)

    async def test_cost_record_repo_implements_protocol(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.repositories import CostRecordRepository

        repo = SQLiteCostRecordRepository(migrated_db)
        assert isinstance(repo, CostRecordRepository)

    async def test_message_repo_implements_protocol(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.repositories import MessageRepository

        repo = SQLiteMessageRepository(migrated_db)
        assert isinstance(repo, MessageRepository)


@pytest.mark.unit
class TestDeserializationFailures:
    """Test deserialization error paths with corrupt data."""

    async def test_row_to_task_corrupt_json(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Corrupt JSON in a tuple field raises QueryError."""
        from synthorg.persistence.errors import QueryError

        await migrated_db.execute(
            """\
INSERT INTO tasks (
    id, title, description, type, priority, project,
    created_by, status, reviewers
) VALUES (
    'corrupt-1', 'Test', 'Test', 'development', 'medium',
    'proj', 'alice', 'created', '{BAD JSON}'
)"""
        )
        await migrated_db.commit()

        repo = SQLiteTaskRepository(migrated_db)
        with pytest.raises(QueryError, match="deserialize task"):
            await repo.get("corrupt-1")

    async def test_row_to_message_corrupt_json(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Corrupt JSON in attachments raises QueryError."""
        from synthorg.persistence.errors import QueryError

        await migrated_db.execute(
            """\
INSERT INTO messages (
    id, timestamp, sender, "to", type, priority,
    channel, content, attachments, metadata
) VALUES (
    'corrupt-msg', '2026-01-01T00:00:00+00:00', 'alice',
    'bob', 'task_update', 'normal', 'general',
    'hello', '{BAD}', '{}'
)"""
        )
        await migrated_db.commit()

        repo = SQLiteMessageRepository(migrated_db)
        with pytest.raises(QueryError, match="deserialize message"):
            await repo.get_history("general")
