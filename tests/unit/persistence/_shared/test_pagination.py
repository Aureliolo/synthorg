"""Unit tests for the pagination drain helpers.

``collect_all`` / ``collect_all_mapping`` reassemble the complete
result of a now-paginated repo method for the callers that genuinely
need the full set (boot-time rehydration, drift detection,
referential-integrity checks) while every underlying query stays
bounded.
"""

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence._shared import collect_all, collect_all_mapping

pytestmark = pytest.mark.unit


class TestCollectAll:
    async def test_drains_every_page_in_order(self) -> None:
        rows = tuple(range(250))
        calls: list[tuple[int, int]] = []

        async def fetch(limit: int, offset: int) -> tuple[int, ...]:
            calls.append((limit, offset))
            return rows[offset : offset + limit]

        result = await collect_all(fetch, page_size=100)

        assert result == rows
        # 100 + 100 + 50 -> a short final page terminates the sweep.
        assert calls == [(100, 0), (100, 100), (100, 200)]

    async def test_exact_multiple_stops_on_empty_page(self) -> None:
        rows = tuple(range(200))
        calls: list[tuple[int, int]] = []

        async def fetch(limit: int, offset: int) -> tuple[int, ...]:
            calls.append((limit, offset))
            return rows[offset : offset + limit]

        # 200 rows / page 100 -> two full pages then an empty page.
        assert await collect_all(fetch, page_size=100) == rows
        # The terminating empty fetch at offset 200 must happen, else
        # an exact-multiple source never stops.
        assert calls == [(100, 0), (100, 100), (100, 200)]

    async def test_empty_source_returns_empty_tuple(self) -> None:
        async def fetch(limit: int, offset: int) -> tuple[int, ...]:
            return ()

        assert await collect_all(fetch, page_size=10) == ()


class TestCollectAllMapping:
    async def test_merges_disjoint_pages(self) -> None:
        full = {f"e{i:03d}": i for i in range(120)}
        ordered = sorted(full.items())

        async def fetch(limit: int, offset: int) -> dict[str, int]:
            return dict(ordered[offset : offset + limit])

        assert await collect_all_mapping(fetch, page_size=50) == full

    async def test_rejects_non_positive_page_size(self) -> None:
        async def fetch(limit: int, offset: int) -> dict[str, int]:
            return {}

        with pytest.raises(QueryError):
            await collect_all_mapping(fetch, page_size=0)

    async def test_empty_source_returns_empty_dict(self) -> None:
        async def fetch(limit: int, offset: int) -> dict[str, int]:
            return {}

        assert await collect_all_mapping(fetch, page_size=10) == {}
