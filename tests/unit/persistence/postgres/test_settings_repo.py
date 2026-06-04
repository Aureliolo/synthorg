"""Unit tests for the Postgres settings CAS helpers."""

from datetime import UTC, datetime

import pytest
import structlog

from synthorg.core.persistence_errors import QueryError
from synthorg.observability.events.settings import SETTINGS_SET_FAILED
from synthorg.persistence.postgres._settings_cas import parse_setting_iso


@pytest.mark.unit
class TestSafeParseIso:
    """``parse_setting_iso`` parses ISO timestamps + logs/raises on bad input."""

    def test_aware_iso_string_round_trips(self) -> None:
        result = parse_setting_iso(
            "2026-04-26T13:00:00+01:00",
            "ns",
            "key",
        )
        assert result == datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_naive_iso_string_logs_and_raises_query_error(self) -> None:
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(QueryError) as raised,
        ):
            parse_setting_iso(
                "2026-04-26T12:00:00",
                "system",
                "default_provider",
            )

        # Wrapped exception preserves the cause chain so callers can
        # inspect the underlying ``ValueError`` if they need to.
        assert isinstance(raised.value.__cause__, ValueError)
        assert "system/default_provider" in str(raised.value)
        assert "'2026-04-26T12:00:00'" in str(raised.value)

        # WARNING emitted with structured fields for operator triage.
        events = [e for e in cap if e.get("event") == SETTINGS_SET_FAILED]
        assert len(events) == 1
        evt = events[0]
        assert evt["namespace"] == "system"
        assert evt["key"] == "default_provider"
        assert evt["value"] == "2026-04-26T12:00:00"
        assert evt["error_type"] == "ValueError"
        # Error description is the redacted ``safe_error_description``;
        # the contract is that it contains the underlying parser
        # message ("timezone-aware") without leaking traceback frame
        # locals.
        assert "timezone-aware" in evt["error"]

    def test_unparseable_iso_string_logs_and_raises(self) -> None:
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(QueryError),
        ):
            parse_setting_iso(
                "not-a-date",
                "ns",
                "key",
            )

        events = [e for e in cap if e.get("event") == SETTINGS_SET_FAILED]
        assert len(events) == 1
        assert events[0]["value"] == "not-a-date"
        assert events[0]["error_type"] == "ValueError"

    def test_iana_timezone_name_logs_and_raises(self) -> None:
        # Underlying ``datetime.fromisoformat`` rejects IANA names; the
        # safe wrapper must still log + raise.
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(QueryError),
        ):
            parse_setting_iso(
                "2026-04-26T12:00:00 Europe/Zurich",
                "ns",
                "key",
            )

        events = [e for e in cap if e.get("event") == SETTINGS_SET_FAILED]
        assert len(events) == 1
        assert events[0]["error_type"] == "ValueError"
