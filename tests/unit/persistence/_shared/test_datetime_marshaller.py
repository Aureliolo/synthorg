"""Tests for the shared ISO 8601 marshalling helpers."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from synthorg.persistence._shared.datetime_marshaller import (
    coerce_row_timestamp,
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

    def test_dst_fall_back_boundary(self) -> None:
        # Europe/Zurich switches from CEST (+02:00) back to CET
        # (+01:00) on the last Sunday of October at 03:00 local, which
        # repeats the 02:00-02:59 hour locally.  Both ``fold=0``
        # (the first, CEST occurrence) and ``fold=1`` (the second,
        # CET occurrence) refer to *different* UTC instants and must
        # both roundtrip through parse + format without collision --
        # this is the exact contract that breaks if a future change
        # ever drops the offset suffix or rewrites it from ``ZoneInfo``
        # context.
        zurich = ZoneInfo("Europe/Zurich")
        cest_first = datetime(2026, 10, 25, 2, 30, tzinfo=zurich, fold=0)
        cet_second = datetime(2026, 10, 25, 2, 30, tzinfo=zurich, fold=1)

        cest_utc = parse_iso_utc(cest_first.isoformat())
        cet_utc = parse_iso_utc(cet_second.isoformat())

        # CEST (+02:00) at 02:30 local == 00:30 UTC; CET (+01:00) at
        # 02:30 local == 01:30 UTC -- distinct instants exactly one
        # hour apart.
        assert cest_utc == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
        assert cet_utc == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
        assert cet_utc - cest_utc == timedelta(hours=1)

        # Both branches survive the format -> parse roundtrip.
        assert parse_iso_utc(format_iso_utc(cest_utc)) == cest_utc
        assert parse_iso_utc(format_iso_utc(cet_utc)) == cet_utc


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


@pytest.mark.unit
class TestCoerceRowTimestamp:
    """``coerce_row_timestamp`` dispatches str/datetime row values."""

    def test_aware_utc_datetime_passthrough(self) -> None:
        value = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

        result = coerce_row_timestamp(value)

        assert result == value
        assert result.tzinfo is UTC

    def test_aware_non_utc_datetime_normalized(self) -> None:
        # psycopg can hand back TIMESTAMPTZ in the session timezone --
        # the dispatcher must converge on UTC.
        plus_one = timezone(timedelta(hours=1))
        value = datetime(2026, 4, 26, 13, 0, tzinfo=plus_one)

        result = coerce_row_timestamp(value)

        assert result == datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_naive_datetime_treated_as_utc(self) -> None:
        # ``normalize_utc`` semantics for the datetime branch: naive
        # is tagged as UTC (matches the project-wide rule "store UTC
        # everywhere").
        result = coerce_row_timestamp(datetime(2026, 4, 26, 12, 0))  # noqa: DTZ001

        assert result == datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_aware_iso_string_parsed(self) -> None:
        result = coerce_row_timestamp("2026-04-26T13:00:00+01:00")

        assert result == datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_z_suffix_iso_string_parsed(self) -> None:
        result = coerce_row_timestamp("2026-04-26T12:00:00Z")

        assert result == datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    def test_naive_iso_string_rejected(self) -> None:
        # Naive strings flow through the strict ``parse_iso_utc`` and
        # raise -- this is what surfaces a corrupt SQLite TEXT row to
        # ``MalformedRowError`` instead of silently UTC-tagging it.
        with pytest.raises(ValueError, match="timezone-aware"):
            coerce_row_timestamp("2026-04-26T12:00:00")

    def test_unparseable_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            coerce_row_timestamp("not-a-date")

    @pytest.mark.parametrize(
        "value",
        [
            12345,
            12.5,
            None,
            ["2026-04-26T12:00:00+00:00"],
            {"timestamp": "2026-04-26T12:00:00+00:00"},
            b"2026-04-26T12:00:00+00:00",
        ],
    )
    def test_unsupported_type_raises_type_error(self, value: object) -> None:
        with pytest.raises(TypeError, match="Unsupported timestamp type"):
            coerce_row_timestamp(value)

    def test_dst_aware_datetime_normalized(self) -> None:
        zurich = ZoneInfo("Europe/Zurich")
        value = datetime(2026, 7, 15, 14, 0, tzinfo=zurich)  # CEST +02:00

        result = coerce_row_timestamp(value)

        assert result == datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_iana_timezone_name_in_iso_string_rejected(self) -> None:
        # ``datetime.fromisoformat`` only accepts numeric UTC offsets
        # or the ``Z`` suffix -- IANA names like ``Europe/Zurich``
        # raise ValueError.  Verify the dispatcher surfaces this
        # cleanly instead of swallowing it.
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            coerce_row_timestamp("2026-04-26T12:00:00 Europe/Zurich")

    def test_zoneinfo_local_isoformat_roundtrip(self) -> None:
        # ``ZoneInfo`` + ``isoformat()`` produces a numeric offset
        # (e.g. ``+02:00``), which ``fromisoformat`` accepts.  Verify
        # the round-trip via the dispatcher behaves exactly like the
        # underlying ``parse_iso_utc``.
        zurich = ZoneInfo("Europe/Zurich")
        value = datetime(2026, 7, 15, 14, 0, tzinfo=zurich)

        from_str = coerce_row_timestamp(value.isoformat())
        from_dt = coerce_row_timestamp(value)

        assert from_str == from_dt
        assert from_str == datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestParseIsoUtcDocstringContract:
    """Regression tests for the precise grammar accepted by parse_iso_utc."""

    def test_named_timezone_in_iso_string_rejected(self) -> None:
        # The docstring explicitly excludes IANA names; protect that
        # contract so a future refactor that swaps ``fromisoformat``
        # for a more permissive parser would surface here.
        with pytest.raises(ValueError, match="Invalid isoformat string"):
            parse_iso_utc("2026-04-26T12:00:00 UTC")

    @pytest.mark.parametrize(
        "value",
        [
            "2026-04-26T12:00:00+05:30",
            "2026-04-26T12:00:00-08:00",
            "2026-04-26T12:00:00+00:00",
            "2026-04-26T12:00:00.000000+00:00",
        ],
    )
    def test_numeric_offset_variants_accepted(self, value: str) -> None:
        result = parse_iso_utc(value)
        assert result.tzinfo is UTC
