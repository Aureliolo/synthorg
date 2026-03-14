"""Tests for in-memory pagination helper."""

import pytest

from synthorg.api.dto import MAX_LIMIT
from synthorg.api.pagination import paginate


@pytest.mark.unit
class TestPaginate:
    def test_empty_collection(self) -> None:
        items: tuple[int, ...] = ()
        page, meta = paginate(items, offset=0, limit=10)
        assert page == ()
        assert meta.total == 0
        assert meta.offset == 0
        assert meta.limit == 10

    def test_normal_slice(self) -> None:
        items = tuple(range(10))
        page, meta = paginate(items, offset=2, limit=3)
        assert page == (2, 3, 4)
        assert meta.total == 10
        assert meta.offset == 2
        assert meta.limit == 3

    def test_offset_beyond_length(self) -> None:
        items = tuple(range(5))
        page, meta = paginate(items, offset=100, limit=10)
        assert page == ()
        assert meta.offset == 5  # clamped to len(items)

    def test_zero_limit_clamped_to_one(self) -> None:
        items = tuple(range(5))
        page, meta = paginate(items, offset=0, limit=0)
        assert len(page) == 1
        assert meta.limit == 1

    def test_limit_exceeds_max_clamped(self) -> None:
        items = tuple(range(5))
        _, meta = paginate(items, offset=0, limit=MAX_LIMIT + 100)
        assert meta.limit == MAX_LIMIT

    def test_negative_offset_clamped_to_zero(self) -> None:
        items = tuple(range(5))
        page, meta = paginate(items, offset=-10, limit=3)
        assert page == (0, 1, 2)
        assert meta.offset == 0

    def test_exact_boundary(self) -> None:
        items = tuple(range(5))
        page, meta = paginate(items, offset=5, limit=10)
        assert page == ()
        assert meta.offset == 5

    def test_meta_fields(self) -> None:
        items = tuple(range(20))
        page, meta = paginate(items, offset=5, limit=3)
        assert meta.total == 20
        assert meta.offset == 5
        assert meta.limit == 3
        assert page == (5, 6, 7)
