"""Smoke tests for the six generic repository protocols.

Verifies each protocol is ``@runtime_checkable`` and that a minimal
duck-typed stub satisfies the structural contract.
"""

from datetime import datetime

import pytest

from synthorg.persistence._generics import (
    AppendOnlyRepository,
    FilteredQueryRepository,
    IdKeyedRepository,
    MVCCRepository,
    SingletonRepository,
    StatefulRepository,
)

pytestmark = pytest.mark.unit


class _SingletonStub:
    async def get(self) -> object | None:
        return None

    async def upsert(self, value: object) -> None:
        _ = value

    async def delete(self) -> bool:
        return False


class _IdKeyedStub:
    async def save(self, entity: object) -> None:
        _ = entity

    async def get(self, entity_id: object) -> object | None:
        _ = entity_id
        return None

    async def delete(self, entity_id: object) -> bool:
        _ = entity_id
        return False

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[object, ...]:
        _ = (limit, offset)
        return ()


class _FilteredQueryStub:
    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[object, ...]:
        _ = (filter_spec, limit, offset)
        return ()

    async def count(self, filter_spec: object) -> int:
        _ = filter_spec
        return 0


class _AppendOnlyStub:
    async def append(self, event: object) -> None:
        _ = event

    async def query(
        self,
        filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[object, ...]:
        _ = (filter_spec, limit, offset)
        return ()

    async def purge_before(self, threshold: datetime) -> int:
        _ = threshold
        return 0


class _StatefulStub:
    async def save(self, entity: object) -> None:
        _ = entity

    async def get(self, entity_id: object) -> object | None:
        _ = entity_id
        return None

    async def delete(self, entity_id: object) -> bool:
        _ = entity_id
        return False

    async def transition_if(
        self,
        entity_id: object,
        from_state: object,
        to_state: object,
        **updates: object,
    ) -> bool:
        _ = (entity_id, from_state, to_state, updates)
        return False


class _MVCCStub:
    async def append_op(self, op: object) -> None:
        _ = op

    async def snapshot_at(
        self, timestamp: datetime, *, limit: int = 100, offset: int = 0
    ) -> tuple[object, ...]:
        _ = (timestamp, limit, offset)
        return ()

    async def get(self, entity_id: object) -> object | None:
        _ = entity_id
        return None

    async def retract(self, entity_id: object, reason: str) -> None:
        _ = (entity_id, reason)

    async def get_operation_log(
        self, entity_id: object, *, limit: int = 100, offset: int = 0
    ) -> tuple[object, ...]:
        _ = (entity_id, limit, offset)
        return ()


def test_singleton_runtime_checkable() -> None:
    assert isinstance(_SingletonStub(), SingletonRepository)


def test_idkeyed_runtime_checkable() -> None:
    assert isinstance(_IdKeyedStub(), IdKeyedRepository)


def test_filtered_query_runtime_checkable() -> None:
    assert isinstance(_FilteredQueryStub(), FilteredQueryRepository)


def test_append_only_runtime_checkable() -> None:
    assert isinstance(_AppendOnlyStub(), AppendOnlyRepository)


def test_stateful_runtime_checkable() -> None:
    assert isinstance(_StatefulStub(), StatefulRepository)


def test_mvcc_runtime_checkable() -> None:
    assert isinstance(_MVCCStub(), MVCCRepository)


def test_negative_runtime_check() -> None:
    """A class missing required methods must NOT satisfy the Protocol."""

    class _Empty:
        pass

    assert not isinstance(_Empty(), SingletonRepository)
    assert not isinstance(_Empty(), IdKeyedRepository)
