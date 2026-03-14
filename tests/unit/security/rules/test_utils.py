"""Tests for walk_string_values utility."""

import pytest

from synthorg.security.rules._utils import walk_string_values

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestWalkStringValuesFlat:
    """Flat dict inputs."""

    def test_empty_dict(self) -> None:
        assert list(walk_string_values({})) == []

    def test_single_string_value(self) -> None:
        result = list(walk_string_values({"a": "hello"}))
        assert result == ["hello"]

    def test_multiple_string_values(self) -> None:
        result = list(walk_string_values({"a": "x", "b": "y"}))
        assert set(result) == {"x", "y"}

    def test_non_string_values_skipped(self) -> None:
        result = list(walk_string_values({"a": 42, "b": None, "c": True}))
        assert result == []

    def test_mixed_types(self) -> None:
        result = list(
            walk_string_values({"a": "found", "b": 42, "c": "also"}),
        )
        assert set(result) == {"found", "also"}


@pytest.mark.unit
class TestWalkStringValuesNested:
    """Nested dict and list inputs."""

    def test_nested_dict(self) -> None:
        data: dict[str, object] = {"outer": {"inner": "deep"}}
        result = list(walk_string_values(data))
        assert result == ["deep"]

    def test_nested_list(self) -> None:
        data: dict[str, object] = {"items": ["one", "two", "three"]}
        result = list(walk_string_values(data))
        assert result == ["one", "two", "three"]

    def test_list_of_dicts(self) -> None:
        data: dict[str, object] = {
            "entries": [{"name": "alice"}, {"name": "bob"}],
        }
        result = list(walk_string_values(data))
        assert set(result) == {"alice", "bob"}

    def test_deeply_nested(self) -> None:
        data: dict[str, object] = {"a": {"b": {"c": [{"d": "found"}]}}}
        result = list(walk_string_values(data))
        assert result == ["found"]

    def test_list_with_mixed_types(self) -> None:
        data: dict[str, object] = {
            "items": ["text", 42, None, {"key": "nested"}, True],
        }
        result = list(walk_string_values(data))
        assert set(result) == {"text", "nested"}

    def test_nested_lists(self) -> None:
        data: dict[str, object] = {"items": [["a", "b"], ["c"]]}
        result = list(walk_string_values(data))
        assert set(result) == {"a", "b", "c"}


@pytest.mark.unit
class TestWalkStringValuesDepthLimit:
    """Depth limit prevents infinite recursion."""

    def test_stops_at_max_depth(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Build a structure deeper than 20 levels — truncated with warning."""
        data: dict[str, object] = {"val": "leaf"}
        for _ in range(25):
            data = {"nested": data}

        result = list(walk_string_values(data))

        # "leaf" is beyond depth limit and should be skipped.
        assert result == []
        captured = capsys.readouterr()
        assert "depth" in captured.out.lower()

    def test_list_recursion_respects_depth_limit(self) -> None:
        """Deeply nested lists stop at max depth without RecursionError."""
        # Build a 25-level nested list structure (no dicts).
        inner: object = "leaf"
        for _ in range(25):
            inner = [inner]
        data: dict[str, object] = {"items": inner}

        result = list(walk_string_values(data))
        # "leaf" is beyond depth limit and should be skipped.
        assert result == []
