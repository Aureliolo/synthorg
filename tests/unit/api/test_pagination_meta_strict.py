"""Regression test: ``PaginationMeta`` rejects legacy ``total`` / ``offset``.

The cursor-only schema (limit + next_cursor + has_more) is the wire
contract. Stale fields from earlier offset-based pagination must raise
``ValidationError`` so an accidental reintroduction surfaces at the
DTO boundary instead of leaking into the response envelope.
"""

import pytest
from pydantic import ValidationError

from synthorg.api.dto import PaginationMeta

pytestmark = pytest.mark.unit


class TestPaginationMetaStrict:
    """Wire shape: ``{limit, next_cursor, has_more}`` -- no legacy fields."""

    def test_canonical_construction_succeeds(self) -> None:
        meta = PaginationMeta(limit=50, next_cursor=None, has_more=False)
        assert meta.limit == 50
        assert meta.next_cursor is None
        assert meta.has_more is False

    def test_legacy_total_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(
                limit=50,
                next_cursor=None,
                has_more=False,
                total=100,  # type: ignore[call-arg]
            )

    def test_legacy_offset_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(
                limit=50,
                next_cursor=None,
                has_more=False,
                offset=10,  # type: ignore[call-arg]
            )

    def test_unknown_field_rejected(self) -> None:
        """``extra="forbid"`` keeps the wire shape narrow."""
        with pytest.raises(ValidationError):
            PaginationMeta(
                limit=50,
                next_cursor=None,
                has_more=False,
                page_count=5,  # type: ignore[call-arg]
            )

    def test_serialised_shape_only_canonical_keys(self) -> None:
        meta = PaginationMeta(limit=20, next_cursor="abc", has_more=True)
        dumped = meta.model_dump(mode="json")
        assert set(dumped.keys()) == {"limit", "next_cursor", "has_more"}
