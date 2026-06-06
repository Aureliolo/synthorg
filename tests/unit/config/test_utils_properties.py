"""Property-based tests for config utility functions (deep_merge, to_float)."""

import copy
from decimal import Decimal
from types import MappingProxyType

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

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
        st.lists(children, max_size=3),
        st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=3),
    ),
    max_leaves=5,
)

_str_key_dicts = st.dictionaries(
    st.text(min_size=1, max_size=10),
    _json_values,
    max_size=5,
)

# ── deep_merge ──────────────────────────────────────────────────


class TestDeepMergeProperties:
    @given(a=_str_key_dicts)
    def test_identity_merge_with_empty(self, a: dict[str, JsonValue]) -> None:
        result = deep_merge(a, {})
        assert result == a
        # Result must be a distinct object (deep copy)
        assert result is not a

    @given(a=_str_key_dicts, b=_str_key_dicts)
    def test_result_keys_are_union(
        self, a: dict[str, JsonValue], b: dict[str, JsonValue]
    ) -> None:
        result = deep_merge(a, b)
        assert set(result.keys()) == set(a.keys()) | set(b.keys())

    @given(a=_str_key_dicts, b=_str_key_dicts)
    def test_inputs_are_not_mutated(
        self, a: dict[str, JsonValue], b: dict[str, JsonValue]
    ) -> None:
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
    def test_recursive_nested_merge(
        self, base: dict[str, JsonValue], override_z: int
    ) -> None:
        override = {"nested": {"z": override_z}}
        result = deep_merge(base, override)
        # ``result["nested"]`` is JsonValue but the strategy guarantees it
        # is a dict-typed branch; narrow once and re-use for the three
        # subscript assertions below.
        result_nested = result["nested"]
        base_nested = base["nested"]
        assert isinstance(result_nested, dict)
        assert isinstance(base_nested, dict)
        # Original nested keys preserved
        assert result_nested["x"] == base_nested["x"]
        assert result_nested["y"] == base_nested["y"]
        # New key added
        assert result_nested["z"] == override_z

    @given(a=_str_key_dicts, b=_str_key_dicts)
    def test_override_values_win_for_non_dict(
        self, a: dict[str, JsonValue], b: dict[str, JsonValue]
    ) -> None:
        result = deep_merge(a, b)
        for key, value in b.items():
            if not (key in a and isinstance(a[key], dict) and isinstance(value, dict)):
                assert result[key] == value

    def test_accepts_non_dict_mapping(self) -> None:
        """Inputs may be any read-only ``Mapping``, not just ``dict``.

        Guards the signature widening from ``dict`` to ``Mapping``: a
        ``MappingProxyType`` at the top level merges and yields a plain
        ``dict`` result.
        """
        base = MappingProxyType({"a": 1, "nested": {"x": 1}})
        override = MappingProxyType({"b": 2})

        result = deep_merge(base, override)

        assert result == {"a": 1, "nested": {"x": 1}, "b": 2}
        assert isinstance(result, dict)


# ── to_float ────────────────────────────────────────────────────


class TestToFloatProperties:
    @given(value=st.integers(min_value=-10_000, max_value=10_000))
    def test_integers_convert(self, value: int) -> None:
        result = to_float(value)
        assert isinstance(result, float)
        assert result == float(value)

    @given(value=st.floats(allow_nan=False, allow_infinity=False))
    def test_floats_pass_through(self, value: float) -> None:
        result = to_float(value)
        assert isinstance(result, float)
        assert result == value

    @given(
        value=st.from_regex(r"-?\d+(\.\d+)?", fullmatch=True),
    )
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
    def test_non_numeric_raises_value_error(self, value: object) -> None:
        with pytest.raises(ValueError, match="numeric value"):
            to_float(value)

    @given(value=st.text().filter(lambda s: not _is_numeric_string(s)))
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

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_rejected(self, value: bool) -> None:
        """``bool`` is rejected though it is an ``int`` subclass.

        A YAML boolean landing in a numeric field is almost always a
        mistake; rejecting it matches the rate-limit override validators.
        """
        with pytest.raises(ValueError, match="numeric value"):
            to_float(value)

    @pytest.mark.parametrize("value", [Decimal("1.5"), Decimal(0)])
    def test_decimal_rejected(self, value: Decimal) -> None:
        """``Decimal`` is rejected by the ``str``/``int``/``float`` gate.

        The old ``float(value)`` accepted any ``__float__`` implementer;
        the narrowed contract no longer does.  No config caller passes a
        ``Decimal`` (YAML yields only str/int/float/bool/None).
        """
        with pytest.raises(ValueError, match="numeric value"):
            to_float(value)
