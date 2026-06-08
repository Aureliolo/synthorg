"""Tests for the shared charter row <-> model marshalling helpers."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    ProjectCharter,
    ScopeBoundaries,
)
from synthorg.persistence._shared.charter_marshalling import (
    CHARTER_COLUMNS,
    as_iso,
    build_charter_where,
    charter_save_params,
    row_to_charter,
    validate_charter_update_keys,
)
from synthorg.persistence.charter_protocol import CharterFilterSpec

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _make_charter(*, approved_forecast_id: UUID | None = None) -> ProjectCharter:
    """Build a charter.

    When *approved_forecast_id* is given the charter is APPROVED with the
    full dispatch provenance the model invariant requires; otherwise it is
    a bare DRAFTED charter (no approval provenance allowed).
    """
    approved = approved_forecast_id is not None
    return ProjectCharter(
        id=NotBlankStr("charter-1"),
        conversation_id=NotBlankStr("conv-1"),
        created_by=NotBlankStr("user-1"),
        status=CharterStatus.APPROVED if approved else CharterStatus.DRAFTED,
        title=NotBlankStr("Better memory layer"),
        brief=NotBlankStr("Build an alternative to the incumbent memory tool."),
        goals=(NotBlankStr("Beat baseline recall"),),
        constraints=(NotBlankStr("Self-hostable"),),
        success_criteria=(NotBlankStr("Recall beats baseline by 10%"),),
        scope=ScopeBoundaries(
            in_scope=(NotBlankStr("retrieval"),),
            out_of_scope=(NotBlankStr("billing"),),
        ),
        envelope=BudgetEnvelope(amount=1000.0, currency="USD", deadline=_NOW),
        proposed_project_name=NotBlankStr("memory-layer"),
        proposed_project_description="A better memory layer.",
        created_at=_NOW,
        updated_at=_NOW,
        approved_at=_NOW if approved else None,
        approved_by=NotBlankStr("approver-1") if approved else None,
        forecast_id=approved_forecast_id,
        correlation_id=NotBlankStr("corr-1") if approved else None,
        task_id=NotBlankStr("task-1") if approved else None,
    )


def _sqlite_row(entity: ProjectCharter) -> dict[str, object]:
    """Build a SQLite-shaped row (TEXT timestamps / string forecast_id)."""
    columns = [c.strip() for c in CHARTER_COLUMNS.split(",")]
    return dict(zip(columns, charter_save_params(entity), strict=True))


@pytest.mark.unit
class TestRowToCharter:
    """``row_to_charter`` reconstructs a charter from either backend shape."""

    def test_sqlite_shape_round_trip(self) -> None:
        charter = _make_charter(approved_forecast_id=uuid4())
        result = row_to_charter(_sqlite_row(charter))

        assert result == charter

    def test_forecast_id_string_parsed_to_uuid(self) -> None:
        forecast = uuid4()
        row = _sqlite_row(_make_charter(approved_forecast_id=forecast))
        result = row_to_charter(row)

        assert result.forecast_id == forecast
        assert isinstance(result.forecast_id, UUID)

    def test_null_forecast_id_stays_none(self) -> None:
        result = row_to_charter(_sqlite_row(_make_charter()))

        assert result.forecast_id is None

    def test_postgres_shape_native_types(self) -> None:
        forecast = uuid4()
        row = _sqlite_row(_make_charter(approved_forecast_id=forecast))
        # Postgres hands back native datetime / UUID rather than TEXT.
        row["created_at"] = _NOW
        row["updated_at"] = _NOW
        row["envelope_deadline"] = _NOW
        row["forecast_id"] = forecast

        result = row_to_charter(row)

        assert result.created_at == _NOW
        assert result.envelope.deadline == _NOW
        assert result.forecast_id == forecast

    def test_corrupt_row_raises_query_error(self) -> None:
        row = _sqlite_row(_make_charter())
        row["envelope_amount"] = "not-a-number"

        with pytest.raises(QueryError):
            row_to_charter(row)


@pytest.mark.unit
class TestBuildCharterWhere:
    """``build_charter_where`` emits backend-specific placeholders."""

    def test_empty_filter_matches_all(self) -> None:
        where, params = build_charter_where(CharterFilterSpec(), placeholder="?")

        assert where == "1=1"
        assert params == []

    def test_sqlite_placeholders(self) -> None:
        spec = CharterFilterSpec(status=CharterStatus.DRAFTED, created_by="user-1")
        where, params = build_charter_where(spec, placeholder="?")

        assert where == "status = ? AND created_by = ?"
        assert params == [CharterStatus.DRAFTED.value, "user-1"]

    def test_postgres_placeholders(self) -> None:
        spec = CharterFilterSpec(project_id="proj-1")
        where, params = build_charter_where(spec, placeholder="%s")

        assert where == "project_id = %s"
        assert params == ["proj-1"]


@pytest.mark.unit
class TestValidateCharterUpdateKeys:
    """``validate_charter_update_keys`` guards the transition update set."""

    def test_allowed_keys_pass(self) -> None:
        validate_charter_update_keys({"updated_at": _NOW, "approved_by": "user-1"})

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(QueryError):
            validate_charter_update_keys({"status": "approved"})


@pytest.mark.unit
class TestAsIso:
    """``as_iso`` normalises transition timestamp updates."""

    def test_none_returns_none(self) -> None:
        assert as_iso(None) is None

    def test_datetime_formatted(self) -> None:
        assert as_iso(_NOW) == "2026-05-22T12:00:00+00:00"

    def test_string_passthrough(self) -> None:
        assert as_iso("2026-05-22T12:00:00+00:00") == "2026-05-22T12:00:00+00:00"
