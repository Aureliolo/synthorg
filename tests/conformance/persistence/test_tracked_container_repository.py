"""Conformance tests for ``TrackedContainerRepository`` (both backends)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.tracked_container_protocol import TrackedContainerRecord

pytestmark = pytest.mark.integration


def _record(
    *,
    container_id: str = "ctr-1",
    sidecar_id: str | None = None,
) -> TrackedContainerRecord:
    return TrackedContainerRecord(
        container_id=NotBlankStr(container_id),
        sidecar_id=NotBlankStr(sidecar_id) if sidecar_id is not None else None,
        created_at=datetime.now(UTC),
    )


class TestTrackedContainerRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.tracked_containers.save(_record(sidecar_id="sc-1"))

        loaded = await backend.tracked_containers.get(NotBlankStr("ctr-1"))
        assert loaded is not None
        assert loaded.container_id == "ctr-1"
        assert loaded.sidecar_id == "sc-1"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.tracked_containers.get(NotBlankStr("ghost")) is None

    async def test_save_upserts(self, backend: PersistenceBackend) -> None:
        await backend.tracked_containers.save(_record(sidecar_id=None))
        await backend.tracked_containers.save(_record(sidecar_id="sc-1"))

        loaded = await backend.tracked_containers.get(NotBlankStr("ctr-1"))
        assert loaded is not None
        assert loaded.sidecar_id == "sc-1"

    async def test_save_nullable_sidecar(self, backend: PersistenceBackend) -> None:
        await backend.tracked_containers.save(_record(sidecar_id=None))

        loaded = await backend.tracked_containers.get(NotBlankStr("ctr-1"))
        assert loaded is not None
        assert loaded.sidecar_id is None

    async def test_load_all(self, backend: PersistenceBackend) -> None:
        await backend.tracked_containers.save(_record(container_id="ctr-1"))
        await backend.tracked_containers.save(_record(container_id="ctr-2"))

        rows = await backend.tracked_containers.load_all()
        ids = {r.container_id for r in rows}
        assert "ctr-1" in ids
        assert "ctr-2" in ids

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.tracked_containers.save(_record())

        deleted = await backend.tracked_containers.delete(NotBlankStr("ctr-1"))
        assert deleted is True

        assert await backend.tracked_containers.get(NotBlankStr("ctr-1")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.tracked_containers.delete(NotBlankStr("ghost"))
        assert deleted is False
