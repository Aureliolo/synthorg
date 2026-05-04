"""Unit tests for synthorg.memory.utils."""

import pytest

from synthorg.memory.utils import deduplicate_tags


@pytest.mark.unit
class TestDeduplicateTags:
    """Tag dedup helper preserves order and removes duplicates."""

    def test_empty_input_returns_empty_tuple(self) -> None:
        assert deduplicate_tags([]) == ()
        assert deduplicate_tags(()) == ()

    def test_already_unique_returns_same_order(self) -> None:
        assert deduplicate_tags(("a", "b", "c")) == ("a", "b", "c")

    def test_removes_duplicates_preserves_first_occurrence(self) -> None:
        assert deduplicate_tags(("a", "b", "a", "c", "b")) == ("a", "b", "c")

    def test_accepts_list_input(self) -> None:
        assert deduplicate_tags(["x", "y", "x"]) == ("x", "y")

    def test_accepts_generator_input(self) -> None:
        assert deduplicate_tags(s for s in ("a", "a", "b")) == ("a", "b")

    def test_preserves_int_tag_types(self) -> None:
        assert deduplicate_tags((1, 2, 1, 3)) == (1, 2, 3)
