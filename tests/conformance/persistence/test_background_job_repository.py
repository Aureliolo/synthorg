"""Conformance tests for ``BackgroundJobRepository`` (both backends)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.background_job_protocol import (
    BackgroundJobRecord,
    BackgroundJobStatus,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _record(
    *,
    job_id: str = "job-1",
    container_id: str = "ctr-1",
    owner_id: str = "agent-1",
    project_id: str | None = None,
    status: BackgroundJobStatus = BackgroundJobStatus.RUNNING,
    pid: int | None = 4242,
    exit_code: int | None = None,
) -> BackgroundJobRecord:
    now = datetime.now(UTC)
    return BackgroundJobRecord(
        job_id=NotBlankStr(job_id),
        container_id=NotBlankStr(container_id),
        owner_id=NotBlankStr(owner_id),
        project_id=NotBlankStr(project_id) if project_id is not None else None,
        command_repr=NotBlankStr("sleep 300"),
        pid=pid,
        status=status,
        exit_code=exit_code,
        output_path=NotBlankStr("/tmp/.synthorg-jobs/job-1/output"),  # noqa: S108
        started_at=now,
        updated_at=now,
        max_duration_seconds=3600.0,
    )


class TestBackgroundJobRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.background_jobs.save(_record())

        loaded = await backend.background_jobs.get(NotBlankStr("job-1"))
        assert loaded is not None
        assert loaded.job_id == "job-1"
        assert loaded.container_id == "ctr-1"
        assert loaded.owner_id == "agent-1"
        assert loaded.status is BackgroundJobStatus.RUNNING
        assert loaded.pid == 4242
        assert loaded.exit_code is None

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.background_jobs.get(NotBlankStr("ghost")) is None

    async def test_save_upserts(self, backend: PersistenceBackend) -> None:
        await backend.background_jobs.save(_record(status=BackgroundJobStatus.RUNNING))
        await backend.background_jobs.save(
            _record(status=BackgroundJobStatus.COMPLETED, exit_code=0)
        )

        loaded = await backend.background_jobs.get(NotBlankStr("job-1"))
        assert loaded is not None
        assert loaded.status is BackgroundJobStatus.COMPLETED
        assert loaded.exit_code == 0

    async def test_save_nullable_project_and_pid(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.background_jobs.save(
            _record(project_id=None, pid=None, status=BackgroundJobStatus.PENDING)
        )

        loaded = await backend.background_jobs.get(NotBlankStr("job-1"))
        assert loaded is not None
        assert loaded.project_id is None
        assert loaded.pid is None

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.background_jobs.save(_record())

        deleted = await backend.background_jobs.delete(NotBlankStr("job-1"))
        assert deleted is True
        assert await backend.background_jobs.get(NotBlankStr("job-1")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.background_jobs.delete(NotBlankStr("ghost"))
        assert deleted is False

    async def test_list_items_orders_by_job_id_and_paginates(
        self, backend: PersistenceBackend
    ) -> None:
        for jid in ("job-3", "job-1", "job-2"):
            await backend.background_jobs.save(_record(job_id=jid))
        rows = await backend.background_jobs.list_items(limit=10)
        ids = [r.job_id for r in rows]
        assert ids == sorted(ids)
        assert {"job-1", "job-2", "job-3"} <= set(ids)
        page = await backend.background_jobs.list_items(limit=1, offset=1)
        assert len(page) == 1
        assert page[0].job_id == ids[1]

    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.background_jobs.list_items(limit=0)
        with pytest.raises(QueryError):
            await backend.background_jobs.list_items(offset=-1)

    async def test_list_items_empty(self, backend: PersistenceBackend) -> None:
        assert await backend.background_jobs.list_items() == ()

    async def test_load_all_empty(self, backend: PersistenceBackend) -> None:
        assert await backend.background_jobs.load_all() == ()

    async def test_load_all(self, backend: PersistenceBackend) -> None:
        await backend.background_jobs.save(_record(job_id="job-1"))
        await backend.background_jobs.save(_record(job_id="job-2"))

        rows = await backend.background_jobs.load_all()
        ids = {r.job_id for r in rows}
        assert "job-1" in ids
        assert "job-2" in ids

    async def test_list_by_container(self, backend: PersistenceBackend) -> None:
        await backend.background_jobs.save(
            _record(job_id="job-1", container_id="ctr-a")
        )
        await backend.background_jobs.save(
            _record(job_id="job-2", container_id="ctr-a")
        )
        await backend.background_jobs.save(
            _record(job_id="job-3", container_id="ctr-b")
        )

        rows = await backend.background_jobs.list_by_container(NotBlankStr("ctr-a"))
        ids = {r.job_id for r in rows}
        assert ids == {"job-1", "job-2"}

    async def test_list_by_container_empty(self, backend: PersistenceBackend) -> None:
        rows = await backend.background_jobs.list_by_container(NotBlankStr("ghost"))
        assert rows == ()

    async def test_count_live_by_owner_counts_pending_and_running_only(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.background_jobs.save(
            _record(
                job_id="job-1", owner_id="agent-x", status=BackgroundJobStatus.PENDING
            )
        )
        await backend.background_jobs.save(
            _record(
                job_id="job-2", owner_id="agent-x", status=BackgroundJobStatus.RUNNING
            )
        )
        await backend.background_jobs.save(
            _record(
                job_id="job-3",
                owner_id="agent-x",
                status=BackgroundJobStatus.COMPLETED,
                exit_code=0,
            )
        )
        await backend.background_jobs.save(
            _record(
                job_id="job-4", owner_id="agent-y", status=BackgroundJobStatus.RUNNING
            )
        )

        assert (
            await backend.background_jobs.count_live_by_owner(NotBlankStr("agent-x"))
            == 2
        )
        assert (
            await backend.background_jobs.count_live_by_owner(NotBlankStr("agent-y"))
            == 1
        )
        assert (
            await backend.background_jobs.count_live_by_owner(NotBlankStr("agent-z"))
            == 0
        )

    async def test_list_by_owner(self, backend: PersistenceBackend) -> None:
        await backend.background_jobs.save(_record(job_id="job-1", owner_id="agent-x"))
        await backend.background_jobs.save(_record(job_id="job-2", owner_id="agent-x"))
        await backend.background_jobs.save(_record(job_id="job-3", owner_id="agent-y"))

        rows = await backend.background_jobs.list_by_owner(NotBlankStr("agent-x"))
        ids = {r.job_id for r in rows}
        assert ids == {"job-1", "job-2"}

    async def test_list_by_owner_empty(self, backend: PersistenceBackend) -> None:
        assert await backend.background_jobs.list_by_owner(NotBlankStr("ghost")) == ()
