"""Tests for ``synthorg.core.collections``."""

import pytest

from synthorg.core.collections import dedupe_preserving_order


@pytest.mark.unit
class TestDedupePreservingOrder:
    @pytest.mark.parametrize(
        ("items", "expected"),
        [
            ([], ()),
            (["a"], ("a",)),
            (["a", "a", "a"], ("a",)),
            (["a", "b", "a", "c", "b"], ("a", "b", "c")),
            ((1, 2, 1, 3), (1, 2, 3)),
            (range(3), (0, 1, 2)),
        ],
    )
    def test_returns_tuple_in_first_seen_order(
        self,
        items: object,
        expected: tuple[object, ...],
    ) -> None:
        assert dedupe_preserving_order(items) == expected  # type: ignore[arg-type]

    def test_accepts_generator(self) -> None:
        """Generators are consumed once -- helper must materialise correctly."""
        gen = (x for x in [3, 1, 3, 2, 1])
        assert dedupe_preserving_order(gen) == (3, 1, 2)
