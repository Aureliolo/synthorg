"""Property-based tests for config loader crash-safety and pass-through."""

from typing import Any

import pytest
from hypothesis import assume, event, given, settings
from hypothesis import strategies as st

from synthorg.config.errors import ConfigParseError, ConfigValidationError
from synthorg.config.loader import _parse_yaml_string, _substitute_env_vars

pytestmark = pytest.mark.unit

# Strategy: text that might contain ${...} patterns
_env_var_text = st.text(
    alphabet=st.sampled_from(
        list("abcdefghijklmnopqrstuvwxyz0123456789_${}: -.\n"),
    ),
    max_size=100,
)

# Strategy: valid YAML-like text
_yaml_text = st.one_of(
    st.just(""),
    st.just("~"),
    st.just("null"),
    st.just("key: value"),
    st.just("a: 1\nb: 2"),
    st.just("nested:\n  x: 1\n  y: 2"),
    st.text(max_size=80),
)


class TestSubstituteEnvVarsProperties:
    @given(
        data=st.dictionaries(
            st.text(min_size=1, max_size=10, alphabet="abcdefghij"),
            st.one_of(
                st.text(max_size=30),
                st.integers(min_value=-100, max_value=100),
                st.floats(allow_nan=False, allow_infinity=False),
                st.booleans(),
                st.none(),
            ),
            max_size=5,
        ),
    )
    @settings(max_examples=100)
    def test_no_env_vars_passes_through(self, data: dict[str, Any]) -> None:
        has_env_pattern = any(
            isinstance(v, str) and "${" in v and "}" in v for v in data.values()
        )
        assume(not has_env_pattern)
        result = _substitute_env_vars(data)
        assert isinstance(result, dict)
        assert result == data
        assert result is not data

    @given(text=_env_var_text)
    @settings(max_examples=100)
    def test_arbitrary_text_with_patterns_no_crash(self, text: str) -> None:
        """Crash-safety: _substitute_env_vars must only raise ConfigValidationError."""
        data = {"key": text}
        try:
            result = _substitute_env_vars(data)
            event("substitute succeeded")
            assert isinstance(result, dict)
            assert "key" in result
            assert isinstance(result["key"], str)
        except ConfigValidationError:
            event("ConfigValidationError raised")
            # Expected for unresolvable ${VAR} patterns without defaults.

    @given(
        data=st.dictionaries(
            st.text(min_size=1, max_size=10, alphabet="abcdefghij"),
            st.integers(min_value=-100, max_value=100),
            max_size=5,
        ),
    )
    @settings(max_examples=50)
    def test_non_string_values_unchanged(self, data: dict[str, Any]) -> None:
        result = _substitute_env_vars(data)
        assert result == data

    def test_env_var_with_default_resolves(self) -> None:
        data = {"key": "${NONEXISTENT_VAR_12345:-fallback}"}
        result = _substitute_env_vars(data)
        assert result["key"] == "fallback"


class TestParseYamlStringProperties:
    @given(text=_yaml_text)
    @settings(max_examples=100)
    def test_arbitrary_text_no_unhandled_exception(self, text: str) -> None:
        """Crash-safety: _parse_yaml_string must only raise ConfigParseError."""
        try:
            result = _parse_yaml_string(text, "<test>")
            event("parse succeeded")
            assert isinstance(result, dict)
        except ConfigParseError:
            event("ConfigParseError raised")
            # Expected for non-mapping or invalid YAML input.

    def test_empty_string_returns_empty_dict(self) -> None:
        assert _parse_yaml_string("", "<test>") == {}

    def test_null_returns_empty_dict(self) -> None:
        assert _parse_yaml_string("null", "<test>") == {}
        assert _parse_yaml_string("~", "<test>") == {}

    def test_valid_yaml_mapping_parsed(self) -> None:
        result = _parse_yaml_string("a: 1\nb: 2", "<test>")
        assert result == {"a": 1, "b": 2}

    def test_non_mapping_raises_config_parse_error(self) -> None:
        with pytest.raises(ConfigParseError):
            _parse_yaml_string("[1, 2, 3]", "<test>")

    @given(
        text=st.text(
            alphabet=st.sampled_from(list("{}[]:,\t\n @!#%^&*")),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=100)
    def test_garbled_yaml_raises_or_returns_dict(self, text: str) -> None:
        """Crash-safety: garbled input must only raise ConfigParseError."""
        try:
            result = _parse_yaml_string(text, "<test>")
            event("garbled parse succeeded")
            assert isinstance(result, dict)
        except ConfigParseError:
            event("ConfigParseError raised")
            # Expected for unparseable input.
