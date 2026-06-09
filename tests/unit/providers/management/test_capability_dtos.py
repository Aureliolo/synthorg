"""Tests for the audit-payload freeze/thaw helpers."""

from types import MappingProxyType

import pytest

from synthorg.providers.management._freeze import (
    _recursively_freeze,
    _recursively_thaw,
)

pytestmark = pytest.mark.unit


class TestRecursivelyFreeze:
    def test_dict_becomes_mapping_proxy(self) -> None:
        frozen = _recursively_freeze({"a": 1})
        assert isinstance(frozen, MappingProxyType)
        assert frozen["a"] == 1

    def test_list_and_tuple_become_tuple(self) -> None:
        assert _recursively_freeze([1, 2]) == (1, 2)
        assert isinstance(_recursively_freeze([1, 2]), tuple)

    def test_nested_containers_frozen_recursively(self) -> None:
        frozen = _recursively_freeze({"outer": {"inner": [1, 2]}})
        assert isinstance(frozen, MappingProxyType)
        assert isinstance(frozen["outer"], MappingProxyType)
        assert frozen["outer"]["inner"] == (1, 2)

    @pytest.mark.parametrize("value", [{1, 2}, frozenset({1, 2})])
    def test_rejects_sets_for_determinism(self, value: object) -> None:
        with pytest.raises(TypeError, match="determinism"):
            _recursively_freeze(value)

    def test_rejects_nested_set(self) -> None:
        with pytest.raises(TypeError, match="determinism"):
            _recursively_freeze({"k": {1, 2}})


class TestRecursivelyThaw:
    @pytest.mark.parametrize("value", [{1, 2}, frozenset({1, 2})])
    def test_rejects_sets(self, value: object) -> None:
        """Thaw keeps the set rejection as a fail-fast for bypassed payloads."""
        with pytest.raises(TypeError, match="determinism"):
            _recursively_thaw(value)


class TestFreezeThawRoundTrip:
    @pytest.mark.parametrize(
        "original",
        [
            {},
            {"a": 1, "b": "x"},
            {"nested": {"deep": [1, 2, 3]}},
            {"mixed": [{"k": "v"}, 1, None, True]},
        ],
    )
    def test_thaw_inverts_freeze(self, original: dict[str, object]) -> None:
        """``_recursively_thaw(_recursively_freeze(x))`` returns plain builtins."""
        restored = _recursively_thaw(_recursively_freeze(original))
        assert restored == original
        assert isinstance(restored, dict)
