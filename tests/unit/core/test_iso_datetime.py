"""Tests for the non-persistence ISO 8601 datetime parsers.

Pins the naive/aware/malformed behaviour that the Mem0, OAuth, and
git-log call sites rely on after the REWORK #10 helper collapse.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from synthorg.core.iso_datetime import (
    is_valid_iso_datetime,
    now_iso_utc,
    parse_git_log_timestamp,
    parse_iso_assume_utc,
)


@pytest.mark.unit
class TestParseIsoAssumeUtc:
    """``parse_iso_assume_utc`` assumes UTC for naive, keeps aware as-is."""

    def test_aware_value_preserves_offset(self) -> None:
        result = parse_iso_assume_utc("2026-06-15T12:00:00+05:00")
        assert result == datetime(
            2026, 6, 15, 12, 0, tzinfo=timezone(timedelta(hours=5))
        )
        # Offset preserved, not re-normalised to UTC.
        assert result.utcoffset() == timedelta(hours=5)

    def test_naive_value_is_assumed_utc(self) -> None:
        result = parse_iso_assume_utc("2026-06-15T12:00:00")
        assert result.tzinfo is UTC
        assert result == datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

    def test_zulu_value_is_utc(self) -> None:
        result = parse_iso_assume_utc("2026-06-15T12:00:00Z")
        assert result == datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

    def test_malformed_value_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            parse_iso_assume_utc("not-a-timestamp")


@pytest.mark.unit
class TestParseGitLogTimestamp:
    """``parse_git_log_timestamp`` rejects naive, keeps aware offset."""

    def test_aware_value_preserves_offset(self) -> None:
        result = parse_git_log_timestamp("2026-06-15T12:00:00+02:00")
        assert result is not None
        assert result.utcoffset() == timedelta(hours=2)

    def test_naive_value_returns_none(self) -> None:
        assert parse_git_log_timestamp("2026-06-15T12:00:00") is None

    def test_malformed_value_returns_none(self) -> None:
        assert parse_git_log_timestamp("garbage") is None

    def test_zulu_value_is_accepted(self) -> None:
        result = parse_git_log_timestamp("2026-06-15T12:00:00Z")
        assert result is not None
        assert result.utcoffset() == timedelta(0)


@pytest.mark.unit
class TestIsValidIsoDatetime:
    """``is_valid_iso_datetime`` is a parseability probe."""

    @pytest.mark.parametrize(
        "value",
        [
            "2026-06-15T12:00:00Z",
            "2026-06-15T12:00:00+02:00",
            "2026-06-15T12:00:00",
            "2026-06-15",
        ],
    )
    def test_parseable_values_return_true(self, value: str) -> None:
        assert is_valid_iso_datetime(value) is True

    @pytest.mark.parametrize("value", ["not-a-timestamp", "", "2026-13-99"])
    def test_unparseable_values_return_false(self, value: str) -> None:
        assert is_valid_iso_datetime(value) is False


@pytest.mark.unit
class TestNowIsoUtc:
    """``now_iso_utc`` returns a UTC-offset ISO 8601 string."""

    def test_returns_parseable_utc_string(self) -> None:
        result = now_iso_utc()
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)
