"""Conformance tests for ``ModelPinValidationRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture in
``tests/conformance/persistence/conftest.py``. The repo is built over
the migrated ``backend.get_db()`` handle.

Covers:

* CRUD round-trip (save / get / list / delete).
* ``get`` returns ``None`` for an absent prompt class.
* ``save`` upsert semantics: re-validating a class replaces the row.
* ``list_items`` ordering (``prompt_class_id`` ASC) + pagination.
* Invalid pagination args raise :class:`QueryError`.
"""

from datetime import UTC, datetime
from typing import cast

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.llm.model_pin_validation import ModelPinValidationRow
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.persistence.model_pin_validation_protocol import (
    ModelPinValidationRepository,
)
from synthorg.persistence.postgres.model_pin_validation_repo import (
    PostgresModelPinValidationRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.model_pin_validation_repo import (
    SQLiteModelPinValidationRepository,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> ModelPinValidationRepository:
    """Return a concrete pin-validation repository bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteModelPinValidationRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresModelPinValidationRepository(
            cast("AsyncConnectionPool", handle),
        )
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _make_row(
    *,
    prompt_class_id: PromptPurposeId = PromptPurposeId.MEMORY_RERANK,
    tier: CapabilityLevel = "small",
) -> ModelPinValidationRow:
    return ModelPinValidationRow(
        prompt_class_id=prompt_class_id,
        validated_at=_NOW,
        tier=tier,
    )


class TestModelPinValidationRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_row())

        fetched = await repo.get(NotBlankStr("system:memory:rerank"))
        assert fetched is not None
        assert fetched.prompt_class_id == PromptPurposeId.MEMORY_RERANK
        assert fetched.tier == "small"
        assert fetched.validated_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(NotBlankStr("system:memory:rerank")) is None

    async def test_save_upsert_replaces_existing(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_row(tier="small"))
        await repo.save(_make_row(tier="medium"))

        fetched = await repo.get(NotBlankStr("system:memory:rerank"))
        assert fetched is not None
        assert fetched.tier == "medium"
        items = await repo.list_items()
        rerank = [
            r for r in items if r.prompt_class_id == PromptPurposeId.MEMORY_RERANK
        ]
        assert len(rerank) == 1

    async def test_list_items_ordered_and_paginated(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_row(prompt_class_id=PromptPurposeId.VERIFICATION))
        await repo.save(_make_row(prompt_class_id=PromptPurposeId.MEMORY_RERANK))
        await repo.save(_make_row(prompt_class_id=PromptPurposeId.RESEARCH_SYNTHESIS))

        items = await repo.list_items()
        ids = [str(r.prompt_class_id) for r in items]
        assert ids == sorted(ids)

        page = await repo.list_items(limit=1, offset=1)
        assert len(page) == 1
        # offset=1 over the ascending order returns the second id, not just
        # any single row.
        assert str(page[0].prompt_class_id) == ids[1]

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_row())
        assert await repo.delete(NotBlankStr("system:memory:rerank")) is True
        assert await repo.delete(NotBlankStr("system:memory:rerank")) is False
        assert await repo.get(NotBlankStr("system:memory:rerank")) is None

    async def test_list_items_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        with pytest.raises(QueryError):
            await repo.list_items(limit=-1)
