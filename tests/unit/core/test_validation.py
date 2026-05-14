"""Tests for shared validation utilities."""

import pytest

from synthorg.core.validation import (
    coerce_positive_int,
    is_valid_action_type,
    require_non_blank,
)

pytestmark = pytest.mark.unit


class TestIsValidActionType:
    """is_valid_action_type() validates category:action format."""

    @pytest.mark.parametrize(
        "valid",
        [
            "deploy:production",
            "db:admin",
            "comms:internal",
            "test:action",
            "a:b",
        ],
    )
    def test_valid_formats(self, valid: str) -> None:
        assert is_valid_action_type(valid) is True

    @pytest.mark.parametrize(
        "invalid",
        [
            "deploy",
            ":release",
            "deploy:",
            "deploy:  ",
            "  :release",
            "a:b:c",
            "",
            "   ",
            "no-colon-at-all",
        ],
    )
    def test_invalid_formats(self, invalid: str) -> None:
        assert is_valid_action_type(invalid) is False


class TestRequireNonBlank:
    """require_non_blank() returns str(value) or raises ValueError."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("alice", "alice"),
            ("  alice  ", "  alice  "),
            ("0", "0"),
            (42, "42"),
        ],
    )
    def test_returns_stringified_value(
        self,
        value: object,
        expected: str,
    ) -> None:
        assert require_non_blank(value, name="field") == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_rejects_blank(self, value: object) -> None:
        with pytest.raises(ValueError, match="field must be a non-blank string"):
            require_non_blank(value, name="field")

    def test_field_name_in_message(self) -> None:
        with pytest.raises(ValueError, match="my_field"):
            require_non_blank(None, name="my_field")


class TestCoercePositiveInt:
    """coerce_positive_int() accepts positive ints / int-strings."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, 1),
            (42, 42),
            ("1", 1),
            ("42", 42),
            (None, 7),
        ],
    )
    def test_valid_inputs(self, value: object, expected: int) -> None:
        assert coerce_positive_int(value, name="x", default=7) == expected

    def test_default_returned_when_none(self) -> None:
        assert coerce_positive_int(None, name="x", default=99) == 99

    @pytest.mark.parametrize("bad", [True, False])
    def test_rejects_bool(self, bad: bool) -> None:
        with pytest.raises(TypeError, match="positive integer, got bool"):
            coerce_positive_int(bad, name="x", default=1)

    @pytest.mark.parametrize("bad", ["abc", "1.5", "x", ""])
    def test_rejects_non_int_string(self, bad: str) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            coerce_positive_int(bad, name="x", default=1)

    @pytest.mark.parametrize("bad", [0, -1, -42, "0", "-5"])
    def test_rejects_non_positive(self, bad: object) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            coerce_positive_int(bad, name="x", default=1)

    def test_rejects_other_types(self) -> None:
        with pytest.raises(TypeError, match="positive integer, got float"):
            coerce_positive_int(1.5, name="x", default=1)
        with pytest.raises(TypeError, match="positive integer, got list"):
            coerce_positive_int([], name="x", default=1)
