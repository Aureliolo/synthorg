"""Tests for the shared ISO 8601 marshalling helpers."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)


@pytest.mark.unit
class TestParseIsoUtc:
    """``parse_iso_utc`` parses ISO 8601 strings to tz-aware UTC."""

    def test_utc_offset_roundtrip_preserves_instant(self) -> None:
        value = "2026-04-26T12:34:56.789012+00:00"
        result = parse_iso_utc(value)

        assert result == datetime(2026, 4, 26, 12, 34, 56, 789_012, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_non_utc_offset_normalized_to_utc(self) -> None:
        # 09:30 in +01:00 == 08:30 in UTC.
        result = parse_iso_utc("2026-04-26T09:30:00+01:00")

        assert result == datetime(2026, 4, 26, 8, 30, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_negative_offset_normalized_to_utc(self) -> None:
        # 23:00 in -05:00 == 04:00 the next day in UTC.
        result = parse_iso_utc("2026-04-26T23:00:00-05:00")

        assert result == datetime(2026, 4, 27, 4, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_z_suffix_treated_as_utc(self) -> None:
        result = parse_iso_utc("2026-04-26T12:00:00Z")

        assert result == datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_microsecond_precision_preserved(self) -> None:
        result = parse_iso_utc("2026-04-26T00:00:00.123456+00:00")

        assert result.microsecond == 123_456

    def test_epoch_boundary(self) -> None:
        result = parse_iso_utc("1970-01-01T00:00:00+00:00")

        assert result == datetime(1970, 1, 1, tzinfo=UTC)

    def test_far_future(self) -> None:
        result = parse_iso_utc("9999-12-31T23:59:59.999999+00:00")

        assert result == datetime(9999, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC)

    def test_naive_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            parse_iso_utc("2026-04-26T12:00:00")

    def test_naive_string_error_message_quotes_value(self) -> None:
        bad = "2026-04-26T12:00:00"
        with pytest.raises(ValueError, match=repr(bad)):
            parse_iso_utc(bad)

    def test_unparseable_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            parse_iso_utc("not-a-date")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            parse_iso_utc("")

    @pytest.mark.parametrize(
        ("iso_string", "expected"),
        [
            (
                "2026-04-26T12:00:00+00:00",
                datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
            ),
            (
                "2026-04-26T13:00:00+01:00",
                datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
            ),
            (
                "2026-04-26T07:00:00-05:00",
                datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
            ),
            (
                "2026-04-27T02:00:00+14:00",
                datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
            ),
            (
                "2026-04-26T00:00:00-12:00",
                datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
            ),
            (
                "2026-04-26T12:00:00Z",
                datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
            ),
        ],
    )
    def test_parametrized_offsets_all_normalize(
        self, iso_string: str, expected: datetime
    ) -> None:
        assert parse_iso_utc(iso_string) == expected

    def test_dst_spring_forward_boundary(self) -> None:
        # Europe/Zurich switches from CET (+01:00) to CEST (+02:00) at
        # 02:00 local on the last Sunday of March; 03:00 local equals
        # 01:00 UTC.
        zurich = ZoneInfo("Europe/Zurich")
        local = datetime(2026, 3, 29, 3, 0, tzinfo=zurich)

        result = parse_iso_utc(local.isoformat())

        assert result == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
        assert result.tzinfo is UTC


@pytest.mark.unit
class TestFormatIsoUtc:
    """``format_iso_utc`` formats tz-aware datetimes as UTC ISO 8601."""

    def test_utc_input_emits_plus_zero_suffix(self) -> None:
        value = datetime(2026, 4, 26, 12, 34, 56, tzinfo=UTC)

        assert format_iso_utc(value) == "2026-04-26T12:34:56+00:00"

    def test_non_utc_input_normalized_to_utc(self) -> None:
        plus_one = timezone(timedelta(hours=1))
        value = datetime(2026, 4, 26, 13, 0, tzinfo=plus_one)

        assert format_iso_utc(value) == "2026-04-26T12:00:00+00:00"

    def test_negative_offset_normalized(self) -> None:
        minus_five = timezone(timedelta(hours=-5))
        value = datetime(2026, 4, 26, 7, 0, tzinfo=minus_five)

        assert format_iso_utc(value) == "2026-04-26T12:00:00+00:00"

    def test_microsecond_precision_preserved(self) -> None:
        value = datetime(2026, 4, 26, 0, 0, 0, 123_456, tzinfo=UTC)

        assert format_iso_utc(value) == "2026-04-26T00:00:00.123456+00:00"

    def test_epoch_boundary(self) -> None:
        assert (
            format_iso_utc(datetime(1970, 1, 1, tzinfo=UTC))
            == "1970-01-01T00:00:00+00:00"
        )

    def test_far_future(self) -> None:
        value = datetime(9999, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC)

        assert format_iso_utc(value) == "9999-12-31T23:59:59.999999+00:00"

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            format_iso_utc(datetime(2026, 4, 26, 12, 0))  # noqa: DTZ001

    def test_zoneinfo_input_normalized(self) -> None:
        zurich = ZoneInfo("Europe/Zurich")
        # Winter time (CET, +01:00).
        value = datetime(2026, 1, 15, 13, 0, tzinfo=zurich)

        assert format_iso_utc(value) == "2026-01-15T12:00:00+00:00"

    def test_dst_summer_input_normalized(self) -> None:
        zurich = ZoneInfo("Europe/Zurich")
        # Summer time (CEST, +02:00).
        value = datetime(2026, 7, 15, 14, 0, tzinfo=zurich)

        assert format_iso_utc(value) == "2026-07-15T12:00:00+00:00"


@pytest.mark.unit
class TestRoundtrip:
    """``format_iso_utc`` then ``parse_iso_utc`` preserves the instant."""

    @pytest.mark.parametrize(
        "value",
        [
            datetime(2026, 4, 26, 12, 34, 56, tzinfo=UTC),
            datetime(2026, 4, 26, 12, 34, 56, 789_012, tzinfo=UTC),
            datetime(1970, 1, 1, tzinfo=UTC),
            datetime(9999, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC),
        ],
    )
    def test_roundtrip_preserves_utc_datetimes(self, value: datetime) -> None:
        assert parse_iso_utc(format_iso_utc(value)) == value

    def test_roundtrip_normalizes_non_utc(self) -> None:
        plus_one = timezone(timedelta(hours=1))
        value = datetime(2026, 4, 26, 13, 0, tzinfo=plus_one)

        result = parse_iso_utc(format_iso_utc(value))

        # Same instant, but normalized to UTC.
        assert result == value
        assert result.tzinfo is UTC
        assert result.utcoffset() == timedelta(0)
