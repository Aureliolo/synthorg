"""Unit tests for the ``Alert`` row marshalling helpers.

Targets ``row_to_alert`` directly (per the module docstring's guidance)
so the strict deserialisation contract is exercised without a live
backend: valid rows round-trip (both SQLite's TEXT-JSON and Postgres's
native list/dict forms), and malformed rows raise ``MalformedRowError``
instead of being silently coerced.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import MalformedRowError
from synthorg.persistence._shared.alert_marshalling import row_to_alert

pytestmark = pytest.mark.unit

_EMITTED = datetime(2026, 6, 20, 12, 5, tzinfo=UTC)
_ALERT_ID = "6f9b3c1e-2a4d-4b7a-9c3e-1a2b3c4d5e6f"


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": _ALERT_ID,
        "severity": "warning",
        "alert_type": "inflection",
        "description": "Quality dropped sharply",
        "affected_domains": '["performance"]',
        "signal_context": '{"metric": "quality"}',
        "recommended_action": None,
        "emitted_at": _EMITTED,
    }
    base.update(overrides)
    return base


class TestRowToAlert:
    def test_valid_row_round_trips_sqlite_text_json(self) -> None:
        alert = row_to_alert(_row())
        assert str(alert.id) == _ALERT_ID
        assert alert.severity.value == "warning"
        assert alert.alert_type == "inflection"
        assert alert.description == "Quality dropped sharply"
        assert alert.affected_domains == ("performance",)
        assert alert.signal_context == {"metric": "quality"}
        assert alert.recommended_action is None
        assert alert.emitted_at == _EMITTED

    def test_valid_row_round_trips_postgres_native_json(self) -> None:
        alert = row_to_alert(
            _row(
                affected_domains=["performance", "budget"],
                signal_context={"metric": "quality", "old_value": 8.0},
                recommended_action="Review recent deploys",
            )
        )
        assert alert.affected_domains == ("performance", "budget")
        assert alert.signal_context == {"metric": "quality", "old_value": 8.0}
        assert alert.recommended_action == "Review recent deploys"

    def test_non_string_id_is_rejected(self) -> None:
        with pytest.raises(MalformedRowError):
            row_to_alert(_row(id=None))

    def test_invalid_severity_is_rejected(self) -> None:
        with pytest.raises(MalformedRowError):
            row_to_alert(_row(severity="urgent"))

    def test_invalid_alert_type_is_rejected(self) -> None:
        with pytest.raises(MalformedRowError):
            row_to_alert(_row(alert_type="unknown"))

    def test_non_array_affected_domains_is_rejected(self) -> None:
        with pytest.raises(MalformedRowError):
            row_to_alert(_row(affected_domains='{"not": "an array"}'))

    def test_non_object_signal_context_is_rejected(self) -> None:
        with pytest.raises(MalformedRowError):
            row_to_alert(_row(signal_context="[1, 2, 3]"))

    def test_missing_column_is_rejected(self) -> None:
        row = _row()
        del row["description"]
        with pytest.raises(MalformedRowError):
            row_to_alert(row)
