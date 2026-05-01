"""Integration test: supervisor launches parallel async research subagents.

Verifies the end-to-end flow: supervisor starts 3 async tasks, each
subagent returns citations, orchestrator consolidates into a final
sources section.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.async_tasks.models import (
    AsyncTaskRecord,
    AsyncTaskStateChannel,
    AsyncTaskStatus,
    TaskSpec,
)
from synthorg.communication.async_tasks.service import AsyncTaskService
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.citation.manager import CitationManager
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.task_engine import TaskEngine


def _make_task(
    *,
    task_id: str,
    status: TaskStatus = TaskStatus.ASSIGNED,
    parent_task_id: str | None = None,
    assigned_to: str | None = None,
    created_by: str = "supervisor-1",
) -> MagicMock:
    """Build a minimal mock Task object."""
    task = MagicMock(spec=Task)
    task.id = task_id
    task.status = status
    task.parent_task_id = parent_task_id
    task.assigned_to = assigned_to
    task.created_by = created_by
    return task


@pytest.mark.integration
class TestAsyncTaskSupervisorFlow:
    """Supervisor launches 3 parallel async research subagents."""

    async def test_supervisor_starts_three_tasks(self) -> None:
        """Supervisor starts 3 async tasks, all get unique IDs."""
        engine = AsyncMock(spec=TaskEngine)
        bus = AsyncMock(spec=MessageBus)
        service = AsyncTaskService(task_engine=engine, message_bus=bus)

        task_mocks = [
            _make_task(task_id=f"task-{i}", status=TaskStatus.CREATED) for i in range(3)
        ]
        engine.create_task.side_effect = task_mocks
        engine.transition_task.side_effect = [(m, None) for m in task_mocks]

        task_ids: list[str] = []
        for i in range(3):
            spec = TaskSpec(
                title=f"Research topic {i}",
                description=f"Investigate topic {i} in depth",
                agent_id=f"researcher-{i}",
                parent_task_id="supervisor-task-0",
            )
            tid = await service.start_async_task(
                supervisor_id="supervisor-1",
                task_spec=spec,
            )
            task_ids.append(tid)

        assert len(task_ids) == 3
        assert len(set(task_ids)) == 3  # All unique

    async def test_list_returns_all_child_tasks(self) -> None:
        """list_async_tasks returns all 3 child tasks with identities."""
        engine = AsyncMock(spec=TaskEngine)
        bus = AsyncMock(spec=MessageBus)
        service = AsyncTaskService(task_engine=engine, message_bus=bus)

        child_tasks = [
            _make_task(
                task_id=f"task-{i}",
                status=TaskStatus.IN_PROGRESS,
                parent_task_id="supervisor-task-0",
                assigned_to=f"researcher-{i}",
            )
            for i in range(3)
        ]
        # Add an unrelated task that should be filtered out
        unrelated = _make_task(
            task_id="unrelated-1",
            status=TaskStatus.IN_PROGRESS,
            parent_task_id="other-supervisor",
        )
        engine.list_tasks.return_value = (
            [*child_tasks, unrelated],
            4,
        )

        children = await service.list_async_tasks("supervisor-task-0")
        assert len(children) == 3
        for tid, status in children:
            assert tid.startswith("task-")
            assert status == AsyncTaskStatus.RUNNING

    def test_citation_consolidation_across_subagents(self) -> None:
        """3 subagents contribute citations, orchestrator deduplicates."""
        manager = CitationManager()

        # Subagent 0 finds 2 sources
        manager = manager.add(
            url="https://example.com/paper-a",
            title="Paper A",
            agent_id="researcher-0",
        )
        manager = manager.add(
            url="https://example.com/paper-b",
            title="Paper B",
            agent_id="researcher-0",
        )

        # Subagent 1 finds 1 new + 1 duplicate (different URL form)
        manager = manager.add(
            url="https://EXAMPLE.COM/paper-a/",  # Same as paper-a
            title="Paper A (duplicate)",
            agent_id="researcher-1",
        )
        manager = manager.add(
            url="https://example.com/paper-c?ref=1&src=2",
            title="Paper C",
            agent_id="researcher-1",
        )

        # Subagent 2 finds 1 new + 1 duplicate (query reordered)
        manager = manager.add(
            url="https://example.com/paper-c?src=2&ref=1",  # Same as paper-c
            title="Paper C (reordered)",
            agent_id="researcher-2",
        )
        manager = manager.add(
            url="https://example.com/paper-d",
            title="Paper D",
            agent_id="researcher-2",
        )

        # Should have 4 unique citations (A, B, C, D)
        assert len(manager.citations) == 4
        assert manager.render_inline("https://example.com/paper-a") == "[1]"
        assert manager.render_inline("https://EXAMPLE.COM/paper-a/") == "[1]"
        assert manager.render_inline("https://example.com/paper-c?src=2&ref=1") == "[3]"

        sources = manager.render_sources_section()
        assert "## Sources" in sources
        assert "[1]" in sources
        assert "[4]" in sources

    def test_citation_handoff_roundtrip(self) -> None:
        """CitationManager survives handoff serialization roundtrip."""
        manager = CitationManager()
        manager = manager.add(
            url="https://example.com/alpha",
            title="Alpha Source",
            agent_id="researcher-0",
        )
        manager = manager.add(
            url="https://example.com/beta",
            title="Beta Source",
            agent_id="researcher-1",
        )

        payload = manager.to_handoff_payload()
        restored = CitationManager.from_handoff_payload(payload)

        assert len(restored.citations) == 2
        assert restored.render_inline("https://example.com/alpha") == "[1]"
        assert restored.render_inline("https://example.com/beta") == "[2]"

    async def test_state_channel_tracks_all_tasks(self) -> None:
        """AsyncTaskStateChannel tracks 3 tasks through lifecycle."""
        now = datetime.now(UTC)
        channel = AsyncTaskStateChannel()

        for i in range(3):
            record = AsyncTaskRecord(
                task_id=f"task-{i}",
                agent_name=f"researcher-{i}",
                status=AsyncTaskStatus.RUNNING,
                created_at=now,
                updated_at=now,
            )
            channel = channel.with_record(record)

        assert len(channel.records) == 3

        # Complete task-1
        channel = channel.with_updated(
            task_id="task-1",
            status=AsyncTaskStatus.COMPLETED,
            updated_at=now + timedelta(minutes=5),
        )
        task_1 = channel.get("task-1")
        assert task_1 is not None
        assert task_1.status == AsyncTaskStatus.COMPLETED

        # Others still running
        task_0 = channel.get("task-0")
        assert task_0 is not None
        assert task_0.status == AsyncTaskStatus.RUNNING
