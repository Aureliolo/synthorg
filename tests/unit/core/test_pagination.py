"""Unit tests for the core pagination helpers and the persistence re-export.

The canonical implementations live in :mod:`synthorg.core.pagination`;
:mod:`synthorg.persistence._shared` re-exports the same objects so the
move is import-surface-preserving (the persistence boundary is unchanged).
"""

import pytest

from synthorg.core.pagination import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    MAX_PAGE_SIZE,
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

    async def test_a_repository_clamping_its_own_page_still_drains(self) -> None:
        """A short page means "this repo clamps", not "the data ran out".

        Repositories cap their own pages (``query`` on the conversation-turn
        repositories stops at ``MAX_PAGE_SIZE``), so a caller asking for more
        than one method's ceiling used to receive that method's first page and
        silently lose every row past it.
        """
        rows = tuple(range(2_500))
        asked: list[int] = []

        async def clamped(limit: int, offset: int) -> tuple[int, ...]:
            asked.append(offset)
            return rows[offset : offset + min(limit, MAX_PAGE_SIZE)]

        assert await collect_all(clamped, page_size=MAX_LIST_LIMIT) == rows
        assert asked == [0, 1_000, 2_000, 2_500]

    async def test_a_mapping_drain_survives_the_same_clamp(self) -> None:
        entries = {n: str(n) for n in range(2_500)}

        async def clamped(limit: int, offset: int) -> dict[int, str]:
            keys = sorted(entries)[offset : offset + min(limit, MAX_PAGE_SIZE)]
            return {k: entries[k] for k in keys}

        assert await collect_all_mapping(clamped, page_size=MAX_LIST_LIMIT) == entries

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
