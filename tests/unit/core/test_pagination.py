"""Unit tests for the core pagination helpers and the persistence re-export.

The canonical implementations live in :mod:`synthorg.core.pagination`;
:mod:`synthorg.persistence._shared` re-exports the same objects so the
move is import-surface-preserving (the persistence boundary is unchanged).
"""

import pytest

from synthorg.core.pagination import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    collect_all,
    collect_all_mapping,
    paginate,
    validate_pagination_args,
)
from synthorg.core.persistence_errors import QueryError

pytestmark = pytest.mark.unit


class TestCanonicalHome:
    def test_persistence_shared_reexports_same_objects(self) -> None:
        from synthorg.persistence import _shared

        assert _shared.DEFAULT_LIST_LIMIT is DEFAULT_LIST_LIMIT
        assert _shared.MAX_LIST_LIMIT is MAX_LIST_LIMIT
        assert _shared.paginate is paginate
        assert _shared.collect_all is collect_all
        assert _shared.collect_all_mapping is collect_all_mapping
        assert _shared.validate_pagination_args is validate_pagination_args

    def test_constants(self) -> None:
        assert DEFAULT_LIST_LIMIT == 100
        assert MAX_LIST_LIMIT == 10_000


class TestPaginate:
    async def test_yields_pages_until_short(self) -> None:
        rows = tuple(range(120))

        async def fetch(limit: int, offset: int) -> tuple[int, ...]:
            return rows[offset : offset + limit]

        pages = [page async for page in paginate(fetch, page_size=50)]

        assert [tuple(p) for p in pages] == [
            tuple(range(50)),
            tuple(range(50, 100)),
            tuple(range(100, 120)),
        ]

    async def test_rejects_non_positive_page_size(self) -> None:
        async def fetch(limit: int, offset: int) -> tuple[int, ...]:
            return tuple(range(offset, offset + limit))

        with pytest.raises(QueryError):
            async for _ in paginate(fetch, page_size=0):
                pass


class TestCollectAll:
    async def test_drains_every_page(self) -> None:
        rows = tuple(range(250))

        async def fetch(limit: int, offset: int) -> tuple[int, ...]:
            return rows[offset : offset + limit]

        assert await collect_all(fetch, page_size=100) == rows


class TestValidatePaginationArgs:
    def test_clamps_to_max(self) -> None:
        assert validate_pagination_args(10**9, 0, event="x") == MAX_LIST_LIMIT

    def test_rejects_bool(self) -> None:
        with pytest.raises(QueryError):
            validate_pagination_args(True, 0, event="x")

    def test_rejects_negative_offset(self) -> None:
        with pytest.raises(QueryError):
            validate_pagination_args(10, -1, event="x")
