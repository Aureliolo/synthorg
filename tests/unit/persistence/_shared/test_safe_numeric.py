"""Tests for safe_int / safe_float row-coercion helpers."""

import pytest

from synthorg.persistence._shared import safe_float, safe_int


@pytest.mark.unit
class TestSafeInt:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, 0),
            (5, 5),
            (5.9, 5),
            ("7", 7),
            ("nope", 0),
            ("", 0),
            (True, 1),
            ([], 0),
        ],
    )
    def test_default_zero(self, value: object, expected: int) -> None:
        assert safe_int(value) == expected

    def test_custom_default(self) -> None:
        assert safe_int(None, default=42) == 42
        assert safe_int("bad", default=42) == 42

    def test_none_default_passthrough(self) -> None:
        assert safe_int(None, default=None) is None
        assert safe_int("3", default=None) == 3


@pytest.mark.unit
class TestSafeFloat:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, 0.0),
            (5, 5.0),
            ("7.5", 7.5),
            ("nope", 0.0),
            (True, 1.0),
            ({}, 0.0),
        ],
    )
    def test_default_zero(self, value: object, expected: float) -> None:
        assert safe_float(value) == expected

    def test_custom_default(self) -> None:
        assert safe_float(None, default=1.5) == 1.5
        assert safe_float("x", default=1.5) == 1.5

    def test_none_default_passthrough(self) -> None:
        assert safe_float(None, default=None) is None
        assert safe_float("2.5", default=None) == 2.5
