"""A deleted entity leaves a record of what it was.

Spend, metrics, approvals and decision records keep the task identifier
after the task is gone, which is what lets a cost row still say what it was
for. That only works while the identifier resolves to something, so a
deletion writes a tombstone naming the entity and the person who removed it.
"""

import pytest

from synthorg.api.controllers._deletion_record import record_deletion
from synthorg.core.deleted_entity import DeletedEntity, DeletedEntityKind
from synthorg.persistence.deleted_entity_protocol import DeletedEntityFilterSpec
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit


async def _tombstones(
    backend: FakePersistenceBackend, entity_id: str
) -> tuple[DeletedEntity, ...]:
    return await backend.deleted_entities.query(
        DeletedEntityFilterSpec(entity_id=entity_id)
    )


class TestRecordDeletion:
    async def test_the_identifier_still_resolves_after_the_row_is_gone(
        self,
    ) -> None:
        backend = FakePersistenceBackend()

        await record_deletion(
            backend,
            kind=DeletedEntityKind.TASK,
            entity_id="task-1",
            display_name="Implement the game engine",
            deleted_by="Aurelio",
        )

        (found,) = await _tombstones(backend, "task-1")
        assert found.display_name == "Implement the game engine"
        assert found.entity_kind is DeletedEntityKind.TASK

    async def test_the_person_who_deleted_it_is_recorded(self) -> None:
        """Only a person deletes an entity, so the record always names one."""
        backend = FakePersistenceBackend()

        await record_deletion(
            backend,
            kind=DeletedEntityKind.PROJECT,
            entity_id="proj-1",
            display_name="Tetris",
            deleted_by="Aurelio",
        )

        (found,) = await _tombstones(backend, "proj-1")
        assert found.deleted_by == "Aurelio"
        assert found.deleted_at.tzinfo is not None

    async def test_a_nameless_entity_is_still_recorded(self) -> None:
        """A thin answer beats losing the fact that it existed."""
        backend = FakePersistenceBackend()

        await record_deletion(
            backend,
            kind=DeletedEntityKind.PLAN,
            entity_id="plan-1",
            display_name=None,
            deleted_by="Aurelio",
        )

        (found,) = await _tombstones(backend, "plan-1")
        assert found.display_name == "(unnamed)"

    async def test_a_blank_display_name_is_treated_as_no_display_name(self) -> None:
        backend = FakePersistenceBackend()

        await record_deletion(
            backend,
            kind=DeletedEntityKind.PLAN,
            entity_id="plan-2",
            display_name="   ",
            deleted_by="Aurelio",
        )

        (found,) = await _tombstones(backend, "plan-2")
        assert found.display_name == "(unnamed)"

    async def test_a_store_failure_never_undoes_the_deletion(self) -> None:
        """The deletion is already decided; the note about it is not the point."""
        backend = FakePersistenceBackend()

        async def _boom(event: DeletedEntity) -> None:
            del event
            msg = "store unavailable"
            raise RuntimeError(msg)

        backend.deleted_entities.append = _boom  # type: ignore[method-assign]

        await record_deletion(
            backend,
            kind=DeletedEntityKind.TASK,
            entity_id="task-2",
            display_name="anything",
            deleted_by="Aurelio",
        )

    async def test_a_reissued_teardown_writes_one_row(self) -> None:
        """The identifier is derived, so a retry lands on the conflict clause."""
        backend = FakePersistenceBackend()

        for _ in range(2):
            await record_deletion(
                backend,
                kind=DeletedEntityKind.TASK,
                entity_id="task-3",
                display_name="Ship it",
                deleted_by="Aurelio",
            )

        assert len(await _tombstones(backend, "task-3")) == 1

    async def test_the_same_id_under_two_kinds_is_two_tombstones(self) -> None:
        """A plan and a task could carry the same identifier; both are recorded."""
        backend = FakePersistenceBackend()

        for kind in (DeletedEntityKind.TASK, DeletedEntityKind.PLAN):
            await record_deletion(
                backend,
                kind=kind,
                entity_id="shared-1",
                display_name="Ambiguous",
                deleted_by="Aurelio",
            )

        assert len(await _tombstones(backend, "shared-1")) == 2
