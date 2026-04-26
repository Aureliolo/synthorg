"""CodSpeed benchmarks for ISO 8601 datetime marshalling.

Every settings DTO, SQLite TEXT column, and JSON envelope round-trip
exercises this pair. A regression here compounds across thousands of
deserialised rows per request.
"""

from datetime import UTC, datetime

import pytest
from pytest_codspeed import BenchmarkFixture

from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    normalize_utc,
    parse_iso_utc,
)

# Pre-built inputs so the bench measures only the function under test.
_AWARE_DT = datetime(2026, 4, 26, 14, 30, 15, 123456, tzinfo=UTC)
_ISO_STRING = "2026-04-26T14:30:15.123456+00:00"
_BATCH_STRINGS = tuple(
    f"2026-04-26T{h:02d}:{m:02d}:00.000000+00:00"
    for h in range(24)
    for m in (0, 15, 30, 45)
)
_BATCH_DATETIMES = tuple(parse_iso_utc(s) for s in _BATCH_STRINGS)


@pytest.mark.benchmark
def test_parse_iso_utc_single(benchmark: BenchmarkFixture) -> None:
    """Parse a single ISO 8601 UTC string."""

    @benchmark
    def _() -> None:
        parse_iso_utc(_ISO_STRING)


@pytest.mark.benchmark
def test_parse_iso_utc_batch_96(benchmark: BenchmarkFixture) -> None:
    """Parse 96 ISO strings (one row per 15min over a day)."""

    @benchmark
    def _() -> None:
        for s in _BATCH_STRINGS:
            parse_iso_utc(s)


@pytest.mark.benchmark
def test_format_iso_utc_single(benchmark: BenchmarkFixture) -> None:
    """Format a single tz-aware datetime to ISO 8601."""

    @benchmark
    def _() -> None:
        format_iso_utc(_AWARE_DT)


@pytest.mark.benchmark
def test_format_iso_utc_batch_96(benchmark: BenchmarkFixture) -> None:
    """Format 96 tz-aware datetimes to ISO 8601."""

    @benchmark
    def _() -> None:
        for dt in _BATCH_DATETIMES:
            format_iso_utc(dt)


@pytest.mark.benchmark
def test_coerce_row_timestamp_string(benchmark: BenchmarkFixture) -> None:
    """Dispatcher path for SQLite TEXT rows (string input)."""

    @benchmark
    def _() -> None:
        coerce_row_timestamp(_ISO_STRING)


@pytest.mark.benchmark
def test_coerce_row_timestamp_datetime(benchmark: BenchmarkFixture) -> None:
    """Dispatcher path for Postgres TIMESTAMPTZ rows (datetime input)."""

    @benchmark
    def _() -> None:
        coerce_row_timestamp(_AWARE_DT)


@pytest.mark.benchmark
def test_normalize_utc(benchmark: BenchmarkFixture) -> None:
    """Normalise an already-aware datetime to UTC (relaxed input path)."""

    @benchmark
    def _() -> None:
        normalize_utc(_AWARE_DT)
