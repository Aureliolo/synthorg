"""Tests for the non-persistence ISO 8601 datetime parsers.

Pins the naive/aware/malformed behaviour that the Mem0, OAuth, and
git-log call sites rely on after the REWORK #10 helper collapse.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from synthorg.core.iso_datetime import (
    format_iso_utc,
    parse_git_log_timestamp,
    parse_iso_assume_utc,
    parse_iso_utc,
)


@pytest.mark.unit
class TestStrictUtcPair:
    """``parse_iso_utc`` / ``format_iso_utc`` reject naive and normalise to UTC."""

    def test_parse_normalises_offset_to_utc(self) -> None:
        result = parse_iso_utc("2026-06-15T12:00:00+05:00")
        assert result == datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
        assert result.utcoffset() == timedelta(0)

    def test_parse_rejects_naive(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            parse_iso_utc("2026-06-15T12:00:00")

    def test_format_emits_utc_offset(self) -> None:
        value = datetime(2026, 6, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))
        assert format_iso_utc(value) == "2026-06-15T10:00:00+00:00"

    def test_format_rejects_naive(self) -> None:
        with pytest.raises(ValueError, match="must be timezone-aware"):
            format_iso_utc(datetime(2026, 6, 15, 12, 0))  # noqa: DTZ001

    def test_persistence_marshaller_reexports_same_objects(self) -> None:
        from synthorg.persistence._shared import datetime_marshaller

        assert datetime_marshaller.parse_iso_utc is parse_iso_utc
        assert datetime_marshaller.format_iso_utc is format_iso_utc


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
