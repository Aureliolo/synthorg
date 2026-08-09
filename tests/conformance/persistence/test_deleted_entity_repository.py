"""Conformance tests for ``DeletedEntityRepository`` (SQLite + Postgres).

The tombstone store is what makes a retained identifier resolvable after the
row it named is gone, so both backends must agree on writing one, reading it
back by that identifier, and refusing to duplicate it.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.deleted_entity import DeletedEntity, DeletedEntityKind
from synthorg.core.types import NotBlankStr
from synthorg.persistence.deleted_entity_protocol import (
    DeletedEntityFilterSpec,
    DeletedEntityRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid

pytestmark = pytest.mark.integration


def _repo(backend: PersistenceBackend) -> DeletedEntityRepository:
    """Return the backend's own ``DeletedEntityRepository``.

    Through the accessor rather than a hand-built repository, so the wiring
    that hands the store to callers is exercised too: a repository that
    works and is never reachable is the failure mode the store exists to
    prevent.

    Returns:
        The backend's tombstone store.
    """
    return backend.deleted_entities


def _tombstone(
    *,
    row_id: str = "tomb-001",
    kind: DeletedEntityKind = DeletedEntityKind.TASK,
    entity_id: str = "task-001",
    display_name: str = "Implement the game engine",
    deleted_by: str = "Aurelio",
    deleted_at: datetime | None = None,
) -> DeletedEntity:
    """Build a tombstone with sensible defaults.

    Returns:
        The tombstone.
    """
    return DeletedEntity(
        id=as_uuid(row_id),
        entity_kind=kind,
        entity_id=NotBlankStr(entity_id),
        display_name=NotBlankStr(display_name),
        deleted_by=NotBlankStr(deleted_by),
        deleted_at=deleted_at or datetime.now(UTC),
    )


class TestDeletedEntityRepository:
    async def test_append_and_read_back_by_identifier(
        self, backend: PersistenceBackend
    ) -> None:
        """The question a surviving record asks: what was this id."""
        repo = _repo(backend)
        await repo.append(_tombstone())

        (found,) = await repo.query(DeletedEntityFilterSpec(entity_id="task-001"))

        assert found.display_name == "Implement the game engine"
        assert found.deleted_by == "Aurelio"
        assert found.entity_kind is DeletedEntityKind.TASK
        assert found.deleted_at.tzinfo is not None

    @pytest.mark.parametrize("kind", list(DeletedEntityKind))
    async def test_every_kind_round_trips(
        self, backend: PersistenceBackend, kind: DeletedEntityKind
    ) -> None:
        """Cases come from the enum, never a hand-written list.

        A hand-written list is a second copy of the enum that no one
        updates, which is exactly how the approvals CHECK came to refuse a
        source the code writes.
        """
        repo = _repo(backend)
        await repo.append(
            _tombstone(row_id=f"tomb-{kind.value}", kind=kind, entity_id=kind.value)
        )

        (found,) = await repo.query(DeletedEntityFilterSpec(entity_id=kind.value))

        assert found.entity_kind is kind

    async def test_appending_the_same_row_twice_is_idempotent(
        self, backend: PersistenceBackend
    ) -> None:
        """A re-issued teardown must not double the record."""
        repo = _repo(backend)
        tombstone = _tombstone(row_id="tomb-dup", entity_id="task-dup")

        await repo.append(tombstone)
        await repo.append(tombstone)

        found = await repo.query(DeletedEntityFilterSpec(entity_id="task-dup"))
        assert len(found) == 1

    async def test_filter_by_kind(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(
            _tombstone(row_id="tomb-t", kind=DeletedEntityKind.TASK, entity_id="t")
        )
        await repo.append(
            _tombstone(row_id="tomb-p", kind=DeletedEntityKind.PROJECT, entity_id="p")
        )

        found = await repo.query(
            DeletedEntityFilterSpec(entity_kind=DeletedEntityKind.PROJECT)
        )

        assert [t.entity_id for t in found] == ["p"]

    async def test_unknown_identifier_returns_nothing(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)

        assert await repo.query(DeletedEntityFilterSpec(entity_id="never")) == ()

    async def test_purge_before_drops_only_older_rows(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        now = datetime.now(UTC)
        await repo.append(
            _tombstone(
                row_id="tomb-old",
                entity_id="old",
                deleted_at=now - timedelta(days=2),
            )
        )
        await repo.append(
            _tombstone(row_id="tomb-new", entity_id="new", deleted_at=now)
        )

        removed = await repo.purge_before(now - timedelta(days=1))

        assert removed == 1
        assert await repo.query(DeletedEntityFilterSpec(entity_id="old")) == ()
        assert len(await repo.query(DeletedEntityFilterSpec(entity_id="new"))) == 1

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        assert isinstance(_repo(backend), DeletedEntityRepository)
