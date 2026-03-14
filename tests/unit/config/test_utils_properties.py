"""Property-based tests for config utility functions (deep_merge, to_float)."""

import copy
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthorg.config.utils import deep_merge, to_float

pytestmark = pytest.mark.unit

# ── Helpers ──────────────────────────────────────────────────


def _is_numeric_string(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


# ── Strategies ──────────────────────────────────────────────────

_json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=50),
)

_json_values = st.recursive(
    _json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=5),
    ),
    max_leaves=20,
)

_str_key_dicts = st.dictionaries(
    st.text(min_size=1, max_size=10),
    _json_values,
    max_size=8,
)


# ── deep_merge ──────────────────────────────────────────────────


class TestDeepMergeProperties:
    @given(a=_str_key_dicts)
    @settings(max_examples=100)
    def test_identity_merge_with_empty(self, a: dict[str, Any]) -> None:
        result = deep_merge(a, {})
        assert result == a
        # Result must be a distinct object (deep copy)
        assert result is not a

    @given(a=_str_key_dicts, b=_str_key_dicts)
    @settings(max_examples=100)
    def test_result_keys_are_union(self, a: dict[str, Any], b: dict[str, Any]) -> None:
        result = deep_merge(a, b)
        assert set(result.keys()) == set(a.keys()) | set(b.keys())

    @given(a=_str_key_dicts, b=_str_key_dicts)
    @settings(max_examples=100)
    def test_inputs_are_not_mutated(self, a: dict[str, Any], b: dict[str, Any]) -> None:
        a_before = copy.deepcopy(a)
        b_before = copy.deepcopy(b)
        deep_merge(a, b)
        assert a == a_before
        assert b == b_before

    @given(
        base=st.fixed_dictionaries(
            {
                "nested": st.fixed_dictionaries(
                    {"x": st.integers(), "y": st.integers()},
                ),
            },
        ),
        override_z=st.integers(),
    )
    @settings(max_examples=100)
    def test_recursive_nested_merge(
        self, base: dict[str, Any], override_z: int
    ) -> None:
        override = {"nested": {"z": override_z}}
        result = deep_merge(base, override)
        # Original nested keys preserved
        assert result["nested"]["x"] == base["nested"]["x"]
        assert result["nested"]["y"] == base["nested"]["y"]
        # New key added
        assert result["nested"]["z"] == override_z

    @given(a=_str_key_dicts, b=_str_key_dicts)
    @settings(max_examples=100)
    def test_override_values_win_for_non_dict(
        self, a: dict[str, Any], b: dict[str, Any]
    ) -> None:
        result = deep_merge(a, b)
        for key, value in b.items():
            if not (key in a and isinstance(a[key], dict) and isinstance(value, dict)):
                assert result[key] == value


# ── to_float ────────────────────────────────────────────────────


class TestToFloatProperties:
    @given(value=st.integers(min_value=-10_000, max_value=10_000))
    @settings(max_examples=100)
    def test_integers_convert(self, value: int) -> None:
        result = to_float(value)
        assert isinstance(result, float)
        assert result == float(value)

    @given(value=st.floats(allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_floats_pass_through(self, value: float) -> None:
        result = to_float(value)
        assert isinstance(result, float)
        assert result == value

    @given(
        value=st.from_regex(r"-?\d+(\.\d+)?", fullmatch=True),
    )
    @settings(max_examples=100)
    def test_numeric_strings_convert(self, value: str) -> None:
        result = to_float(value)
        assert isinstance(result, float)
        assert result == float(value)

    @given(
        value=st.one_of(
            st.just(None),
            st.lists(st.integers(), max_size=3),
            st.dictionaries(st.text(max_size=5), st.integers(), max_size=3),
        ),
    )
    @settings(max_examples=50)
    def test_non_numeric_raises_value_error(self, value: object) -> None:
        with pytest.raises(ValueError, match="numeric value"):
            to_float(value)

    @given(value=st.text().filter(lambda s: not _is_numeric_string(s)))
    @settings(max_examples=50)
    def test_non_numeric_strings_raise_value_error(self, value: str) -> None:
        with pytest.raises(ValueError, match="numeric value"):
            to_float(value)

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "infinity"])
    def test_special_float_strings_accepted(self, value: str) -> None:
        """Document that to_float accepts Python's special float strings.

        These are accepted by Python's float() builtin, so to_float
        also accepts them. Callers that need to reject NaN/Inf should
        validate after conversion (e.g. Pydantic allow_nan=False).
        """
        result = to_float(value)
        assert isinstance(result, float)
