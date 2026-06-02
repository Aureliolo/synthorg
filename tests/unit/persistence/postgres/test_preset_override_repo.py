# mypy: disable-error-code="explicit-any"
"""Hermetic unit tests for ``PostgresPresetOverrideRepo.get`` fail-closed.

A corrupt persisted row must surface as the domain ``QueryError``
(matching ``list_items``) rather than letting a raw deserialization
exception escape the persistence boundary as a 500.
"""

from contextlib import asynccontextmanager
from typing import Any

import pytest
from typeguard import suppress_type_checks

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.postgres.preset_override_repo import (
    PostgresPresetOverrideRepo,
)

pytestmark = pytest.mark.unit


class _FakeCursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        return None

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeConnection:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def cursor(self, row_factory: Any = None) -> _FakeCursor:
        return _FakeCursor(self._row)


class _FakePool:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    @asynccontextmanager
    async def connection(self) -> Any:
        yield _FakeConnection(self._row)


async def test_get_translates_corrupt_row_to_query_error() -> None:
    """A row that fails deserialization raises ``QueryError`` from get()."""
    corrupt = {
        "preset_name": "p1",
        "default_models": None,
        "supported_auth_types": None,
        "candidate_urls": None,
        "base_url": None,
        # ``updated_at`` is required by ``_row_to_override``; an int is
        # not a valid timestamp and trips the fail-closed guard.
        "updated_at": 12345,
        "updated_by": "operator",
    }
    repo = PostgresPresetOverrideRepo(_FakePool(corrupt))  # type: ignore[arg-type]

    # The corrupt ``updated_at`` int trips the repository's fail-closed guard;
    # suppress typeguard so that guard (QueryError) runs instead of typeguard
    # rejecting the int against the datetime annotation first.
    with pytest.raises(QueryError), suppress_type_checks():
        await repo.get(NotBlankStr("p1"))


async def test_get_returns_none_when_absent() -> None:
    """No row -> ``None`` (the guard does not fire)."""
    repo = PostgresPresetOverrideRepo(_FakePool(None))  # type: ignore[arg-type]

    assert await repo.get(NotBlankStr("missing")) is None
