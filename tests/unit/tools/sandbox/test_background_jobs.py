"""Tests for BackgroundJobRegistry."""

from datetime import UTC, datetime

import pytest

from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.tools.sandbox.background_jobs import BackgroundJobRegistry
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class _InMemoryBackgroundJobRepository:
    """Minimal in-memory double satisfying ``BackgroundJobRepository``."""

    def __init__(self) -> None:
        self._rows: dict[str, BackgroundJobRecord] = {}

    async def save(self, entity: BackgroundJobRecord, /) -> None:
        self._rows[entity.job_id] = entity

    async def get(self, entity_id: str, /) -> BackgroundJobRecord | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: str, /) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:
        ordered = sorted(self._rows.values(), key=lambda r: r.job_id)
        return tuple(ordered[offset : offset + limit])

    async def load_all(self) -> tuple[BackgroundJobRecord, ...]:
        return tuple(self._rows.values())

    async def list_by_container(
        self, container_id: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:
        matches = [r for r in self._rows.values() if r.container_id == container_id]
        return tuple(matches[offset : offset + limit])

    async def count_live_by_owner(self, owner_id: str) -> int:
        return sum(
            1
            for r in self._rows.values()
            if r.owner_id == owner_id
            and r.status in {BackgroundJobStatus.PENDING, BackgroundJobStatus.RUNNING}
        )

    async def list_by_owner(
        self, owner_id: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:
        matches = [r for r in self._rows.values() if r.owner_id == owner_id]
        return tuple(matches[offset : offset + limit])


def _record(
    job_id: str,
    *,
    container_id: str = "container-1",
    owner_id: str = "agent-1",
    status: BackgroundJobStatus = BackgroundJobStatus.RUNNING,
    pid: int | None = 123,
    started_at: datetime | None = None,
    max_duration_seconds: float = 3600.0,
) -> BackgroundJobRecord:
    now = started_at or datetime(2026, 1, 1, tzinfo=UTC)
    return BackgroundJobRecord(
        job_id=job_id,
        container_id=container_id,
        owner_id=owner_id,
        command_repr="sleep 30",
        pid=pid,
        status=status,
        output_path="/tmp/.synthorg-jobs/" + job_id + "/output",  # noqa: S108
        started_at=now,
        updated_at=now,
        max_duration_seconds=max_duration_seconds,
    )


class TestBasicReadWrite:
    async def test_save_and_get_round_trip(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        record = _record("job-1")

        await registry.save(record)

        assert await registry.get("job-1") == record

    async def test_get_missing_returns_none(self) -> None:
        registry = BackgroundJobRegistry(_InMemoryBackgroundJobRepository())
        assert await registry.get("ghost") is None

    async def test_count_live_by_owner_excludes_terminal_statuses(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await registry.save(_record("job-a", status=BackgroundJobStatus.RUNNING))
        await registry.save(_record("job-b", status=BackgroundJobStatus.PENDING))
        await registry.save(_record("job-c", status=BackgroundJobStatus.COMPLETED))

        assert await registry.count_live_by_owner("agent-1") == 2

    async def test_list_by_owner_scopes_to_owner(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await registry.save(_record("job-a", owner_id="agent-1"))
        await registry.save(_record("job-b", owner_id="agent-2"))

        result = await registry.list_by_owner("agent-1")

        assert [r.job_id for r in result] == ["job-a"]


class TestListLiveByContainer:
    async def test_excludes_terminal_rows(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await registry.save(
            _record("job-live", container_id="c1", status=BackgroundJobStatus.RUNNING)
        )
        await registry.save(
            _record("job-done", container_id="c1", status=BackgroundJobStatus.COMPLETED)
        )

        result = await registry.list_live_by_container("c1")

        assert [r.job_id for r in result] == ["job-live"]

    async def test_scopes_to_container(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await registry.save(_record("job-a", container_id="c1"))
        await registry.save(_record("job-b", container_id="c2"))

        result = await registry.list_live_by_container("c1")

        assert [r.job_id for r in result] == ["job-a"]


class TestMarkTerminal:
    async def test_updates_status_and_timestamp(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        clock = FakeClock()
        registry = BackgroundJobRegistry(repo, clock=clock)
        record = _record("job-1")
        await registry.save(record)
        clock.advance(60)

        updated = await registry.mark_terminal(
            record, BackgroundJobStatus.COMPLETED, exit_code=0
        )

        assert updated.status == BackgroundJobStatus.COMPLETED
        assert updated.exit_code == 0
        assert updated.updated_at == clock.now()
        assert (await registry.get("job-1")) == updated

    async def test_preserves_existing_exit_code_when_not_given(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        record = _record("job-1").model_copy(update={"exit_code": 7})

        updated = await registry.mark_terminal(record, BackgroundJobStatus.CANCELLED)

        assert updated.exit_code == 7


class TestExpireOverdue:
    async def test_kills_and_marks_timed_out_job_past_ceiling(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        clock = FakeClock()
        registry = BackgroundJobRegistry(repo, clock=clock)
        record = _record(
            "job-slow",
            container_id="c1",
            started_at=clock.now(),
            max_duration_seconds=10.0,
        )
        await registry.save(record)
        clock.advance(11)

        killed: list[tuple[str, int]] = []

        async def kill_fn(container_id: str, pid: int) -> None:
            killed.append((container_id, pid))

        still_live = await registry.expire_overdue("c1", kill_fn=kill_fn)

        assert still_live == ()
        assert killed == [("c1", 123)]
        updated = await registry.get("job-slow")
        assert updated is not None
        assert updated.status == BackgroundJobStatus.TIMED_OUT

    async def test_leaves_job_under_ceiling_untouched(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        clock = FakeClock()
        registry = BackgroundJobRegistry(repo, clock=clock)
        record = _record(
            "job-fast",
            container_id="c1",
            started_at=clock.now(),
            max_duration_seconds=3600.0,
        )
        await registry.save(record)
        clock.advance(5)

        killed: list[tuple[str, int]] = []

        async def kill_fn(container_id: str, pid: int) -> None:
            killed.append((container_id, pid))

        still_live = await registry.expire_overdue("c1", kill_fn=kill_fn)

        assert [r.job_id for r in still_live] == ["job-fast"]
        assert killed == []
        updated = await registry.get("job-fast")
        assert updated is not None
        assert updated.status == BackgroundJobStatus.RUNNING

    async def test_does_not_call_kill_fn_when_pid_never_confirmed(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        clock = FakeClock()
        registry = BackgroundJobRegistry(repo, clock=clock)
        record = _record(
            "job-pending",
            container_id="c1",
            status=BackgroundJobStatus.PENDING,
            pid=None,
            started_at=clock.now(),
            max_duration_seconds=10.0,
        )
        await registry.save(record)
        clock.advance(11)

        killed: list[tuple[str, int]] = []

        async def kill_fn(container_id: str, pid: int) -> None:
            killed.append((container_id, pid))

        still_live = await registry.expire_overdue("c1", kill_fn=kill_fn)

        assert still_live == ()
        assert killed == []
        updated = await registry.get("job-pending")
        assert updated is not None
        assert updated.status == BackgroundJobStatus.TIMED_OUT


class TestReapForContainer:
    async def test_marks_live_rows_orphaned(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await registry.save(
            _record("job-a", container_id="c1", status=BackgroundJobStatus.RUNNING)
        )
        await registry.save(
            _record("job-b", container_id="c1", status=BackgroundJobStatus.PENDING)
        )

        await registry.reap_for_container("c1", reason="container_destroyed")

        job_a = await registry.get("job-a")
        job_b = await registry.get("job-b")
        assert job_a is not None
        assert job_b is not None
        assert job_a.status == BackgroundJobStatus.ORPHANED
        assert job_b.status == BackgroundJobStatus.ORPHANED

    async def test_leaves_terminal_rows_untouched(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await registry.save(
            _record("job-done", container_id="c1", status=BackgroundJobStatus.COMPLETED)
        )

        await registry.reap_for_container("c1", reason="container_destroyed")

        job = await registry.get("job-done")
        assert job is not None
        assert job.status == BackgroundJobStatus.COMPLETED

    async def test_leaves_other_containers_untouched(self) -> None:
        repo = _InMemoryBackgroundJobRepository()
        registry = BackgroundJobRegistry(repo, clock=FakeClock())
        await registry.save(
            _record("job-a", container_id="c1", status=BackgroundJobStatus.RUNNING)
        )
        await registry.save(
            _record("job-b", container_id="c2", status=BackgroundJobStatus.RUNNING)
        )

        await registry.reap_for_container("c1", reason="container_destroyed")

        job_b = await registry.get("job-b")
        assert job_b is not None
        assert job_b.status == BackgroundJobStatus.RUNNING
