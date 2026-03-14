"""Tests for shared configuration utilities."""

import pytest

from synthorg.config.utils import deep_merge


@pytest.mark.unit
class TestDeepMerge:
    def test_empty_base(self) -> None:
        result = deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_empty_override(self) -> None:
        result = deep_merge({"a": 1}, {})
        assert result == {"a": 1}

    def test_both_empty(self) -> None:
        result = deep_merge({}, {})
        assert result == {}

    def test_simple_override(self) -> None:
        result = deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_nested_merge(self) -> None:
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_deeply_nested(self) -> None:
        base = {"x": {"y": {"z": 1, "w": 2}}}
        override = {"x": {"y": {"z": 99}}}
        result = deep_merge(base, override)
        assert result == {"x": {"y": {"z": 99, "w": 2}}}

    def test_override_dict_with_scalar(self) -> None:
        base = {"x": {"a": 1}}
        override = {"x": 42}
        result = deep_merge(base, override)
        assert result == {"x": 42}

    def test_override_scalar_with_dict(self) -> None:
        base = {"x": 42}
        override = {"x": {"a": 1}}
        result = deep_merge(base, override)
        assert result == {"x": {"a": 1}}

    def test_does_not_mutate_base(self) -> None:
        base = {"x": {"a": 1}}
        override = {"x": {"b": 2}}
        original_base = {"x": {"a": 1}}
        deep_merge(base, override)
        assert base == original_base

    def test_does_not_mutate_override(self) -> None:
        base = {"x": 1}
        override = {"y": {"a": [1, 2]}}
        original_override = {"y": {"a": [1, 2]}}
        deep_merge(base, override)
        assert override == original_override

    def test_list_replaced_not_merged(self) -> None:
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = deep_merge(base, override)
        assert result == {"items": [4, 5]}
