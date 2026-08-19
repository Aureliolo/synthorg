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

    async def test_a_fetch_that_ignores_offset_still_terminates(self) -> None:
        """Boundedness is the property the short-page rule buys.

        A fetch that hands back rows regardless of offset is a broken fetch,
        and the sweep has to fail finitely against it rather than accumulate
        pages until the process dies. Terminating on an empty page instead
        would spin here, which is what makes the short page the terminator.
        """
        calls = 0

        async def stuck(limit: int, offset: int) -> tuple[int, ...]:
            nonlocal calls
            calls += 1
            del limit, offset
            return (1,)

        assert await collect_all(stuck, page_size=50) == (1,)
        assert calls == 1

    async def test_the_ask_is_bounded_by_one_ceiling_only(self) -> None:
        """A page has exactly one ceiling, and this is the one that applies.

        A repository clamping its own ``limit`` lower would answer a larger
        request with a full-but-short page, which this reads as the end of the
        data; there is deliberately no second cap here to compensate, because
        one would tax every drain and still be a repository away from wrong.
        """
        asked: list[int] = []

        async def honest(limit: int, offset: int) -> tuple[int, ...]:
            asked.append(limit)
            del offset
            return ()

        assert await collect_all(honest, page_size=MAX_LIST_LIMIT * 2) == ()
        assert asked == [MAX_LIST_LIMIT]

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
