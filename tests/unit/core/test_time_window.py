"""Tests for the rolling-window day-label parsers."""

import pytest

from synthorg.core.time_window import parse_window_days, parse_window_days_strict


@pytest.mark.unit
class TestParseWindowDays:
    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("7d", 7),
            ("30d", 30),
            ("90d", 90),
            ("0d", 0),
            ("7days", None),
            ("7", None),
            ("d", None),
            ("", None),
            ("+7d", None),
        ],
    )
    def test_lenient(self, label: str, expected: int | None) -> None:
        assert parse_window_days(label) == expected


@pytest.mark.unit
class TestParseWindowDaysStrict:
    def test_valid(self) -> None:
        assert parse_window_days_strict("30d") == 30

    @pytest.mark.parametrize("label", ["7days", "7", "d", ""])
    def test_invalid_raises(self, label: str) -> None:
        with pytest.raises(ValueError, match="Unrecognized window size format"):
            parse_window_days_strict(label)
