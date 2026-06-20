"""Conformance tests for ``ExperimentRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture. The repo is built over the
migrated ``backend.get_db()`` handle.

Covers variant CRUD (save / upsert / list-ordered / delete) and
assignment semantics (insert-once with ``ConflictError`` on a repeat,
get, and ``list_assignments`` page + total ordering).
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import aiosqlite
import pytest

from synthorg.core.domain_errors import ConflictError
from synthorg.core.types import NotBlankStr
from synthorg.experiments.models import ExperimentAssignment, ExperimentVariant
from synthorg.persistence.experiment_protocol import ExperimentRepository
from synthorg.persistence.postgres.experiment_repo import PostgresExperimentRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.experiment_repo import SQLiteExperimentRepository

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> ExperimentRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteExperimentRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresExperimentRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _variant(
    *,
    experiment: str = "exp-1",
    variant: str = "control",
    weight: int = 50,
    created_at: datetime = _NOW,
) -> ExperimentVariant:
    return ExperimentVariant(
        experiment=NotBlankStr(experiment),
        variant=NotBlankStr(variant),
        weight=weight,
        description="",
        created_at=created_at,
    )


def _assignment(
    *,
    experiment: str = "exp-1",
    subject_id: str = "subject-1",
    variant: str = "control",
    assigned_at: datetime = _NOW,
) -> ExperimentAssignment:
    return ExperimentAssignment(
        experiment=NotBlankStr(experiment),
        subject_id=NotBlankStr(subject_id),
        variant=NotBlankStr(variant),
        assigned_at=assigned_at,
    )


class TestExperimentVariants:
    async def test_save_and_list_ordered(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_variant(variant="b", created_at=_NOW + timedelta(seconds=1)))
        await repo.save(_variant(variant="a", created_at=_NOW))

        variants = await repo.list_for_experiment(NotBlankStr("exp-1"))
        # Oldest-first by created_at so the assignment hash walk is stable.
        assert [v.variant for v in variants] == ["a", "b"]

    async def test_save_upsert_replaces(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_variant(weight=10))
        await repo.save(_variant(weight=90))

        variants = await repo.list_for_experiment(NotBlankStr("exp-1"))
        assert len(variants) == 1
        assert variants[0].weight == 90

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_variant())
        assert (
            await repo.delete(
                experiment=NotBlankStr("exp-1"), variant=NotBlankStr("control")
            )
            is True
        )
        assert (
            await repo.delete(
                experiment=NotBlankStr("exp-1"), variant=NotBlankStr("control")
            )
            is False
        )
        assert await repo.list_for_experiment(NotBlankStr("exp-1")) == ()


class TestExperimentAssignments:
    async def test_record_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        # An assignment references a registered variant (FK), so seed it first.
        await repo.save(_variant(variant="control"))
        await repo.record_assignment(_assignment())
        fetched = await repo.get_assignment(
            experiment=NotBlankStr("exp-1"), subject_id=NotBlankStr("subject-1")
        )
        assert fetched is not None
        assert fetched.variant == "control"
        assert fetched.assigned_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert (
            await repo.get_assignment(
                experiment=NotBlankStr("exp-1"), subject_id=NotBlankStr("nope")
            )
            is None
        )

    async def test_record_is_insert_once(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        # Seed both variants so the only constraint the repeat insert can
        # violate is the (experiment, subject_id) primary key, not the FK.
        await repo.save(_variant(variant="control"))
        await repo.save(_variant(variant="treatment"))
        await repo.record_assignment(_assignment(variant="control"))
        # A repeat for the same (experiment, subject) conflicts; the
        # service re-reads the canonical first-writer assignment.
        with pytest.raises(ConflictError):
            await repo.record_assignment(_assignment(variant="treatment"))
        fetched = await repo.get_assignment(
            experiment=NotBlankStr("exp-1"), subject_id=NotBlankStr("subject-1")
        )
        assert fetched is not None
        assert fetched.variant == "control"

    async def test_list_assignments_page_and_total(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_variant(variant="control"))
        for index in range(3):
            await repo.record_assignment(
                _assignment(
                    subject_id=f"subject-{index}",
                    assigned_at=_NOW + timedelta(seconds=index),
                )
            )

        page, total = await repo.list_assignments(
            NotBlankStr("exp-1"), limit=2, offset=0
        )
        assert total == 3
        # Newest-first ordering.
        assert [a.subject_id for a in page] == ["subject-2", "subject-1"]
