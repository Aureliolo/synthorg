"""Unit tests for the ``EvolutionOutcomeRecord`` row marshalling helpers.

Targets ``row_to_outcome_record`` directly (per the module docstring's
guidance) so the strict deserialisation contract is exercised without a
live backend: valid rows round-trip, and malformed rows raise
``MalformedRowError`` instead of being silently coerced.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import MalformedRowError
from synthorg.persistence._shared.evolution_outcome_marshalling import (
    row_to_outcome_record,
)

pytestmark = pytest.mark.unit

_PROPOSED = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
_RECORDED = datetime(2026, 6, 20, 12, 5, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "agent_id": "agent-1",
        "axis": "prompt_tuning",
        "applied": True,
        "proposed_at": _PROPOSED,
        "recorded_at": _RECORDED,
    }
    base.update(overrides)
    return base


class TestRowToOutcomeRecord:
    def test_valid_row_round_trips(self) -> None:
        record = row_to_outcome_record(_row())
        assert record.agent_id == "agent-1"
        assert record.axis == "prompt_tuning"
        assert record.applied is True

    def test_sqlite_integer_applied_is_accepted(self) -> None:
        assert row_to_outcome_record(_row(applied=0)).applied is False
        assert row_to_outcome_record(_row(applied=1)).applied is True

    def test_none_agent_id_is_rejected(self) -> None:
        # ``str(None) -> "None"`` would have smuggled a bogus id past the
        # NotBlankStr validator; the strict isinstance check rejects it.
        with pytest.raises(MalformedRowError):
            row_to_outcome_record(_row(agent_id=None))

    def test_blank_agent_id_is_rejected(self) -> None:
        with pytest.raises(MalformedRowError):
            row_to_outcome_record(_row(agent_id="   "))

    def test_non_string_axis_is_rejected(self) -> None:
        with pytest.raises(MalformedRowError):
            row_to_outcome_record(_row(axis=42))

    @pytest.mark.parametrize("bad_applied", [2, -1, "true", None])
    def test_non_boolean_applied_is_rejected(self, bad_applied: object) -> None:
        with pytest.raises(MalformedRowError):
            row_to_outcome_record(_row(applied=bad_applied))

    def test_missing_column_is_rejected(self) -> None:
        row = _row()
        del row["axis"]
        with pytest.raises(MalformedRowError):
            row_to_outcome_record(row)
