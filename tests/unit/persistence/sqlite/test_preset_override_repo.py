"""Tests for ``SQLitePresetOverrideRepo`` corruption / type-validation paths.

The conformance suite at
``tests/conformance/persistence/test_preset_override_repository.py``
covers the happy-path round-trips against both backends.  This unit
suite exercises the SQLite-specific deserialisation guards that
``_row_to_override`` raises ``QueryError`` for: corrupt JSON, wrong
JSON shape, and bad scalar columns.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosqlite.core import Connection

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.sqlite.preset_override_repo import (
    SQLitePresetOverrideRepo,
)
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit


def _make_row(  # noqa: PLR0913 -- test factory with explicit knobs
    *,
    preset_name: object = "test-cloud-provider",
    default_models: object = None,
    supported_auth_types: object = None,
    candidate_urls: object = None,
    base_url: object = "https://api.example.com/v1",
    updated_at: object = None,
    updated_by: object = "user-1",
) -> dict[str, object]:
    """Build a row dict with the persisted column shape."""
    if updated_at is None:
        updated_at = datetime.now(UTC).isoformat()
    return {
        "preset_name": preset_name,
        "default_models": default_models,
        "supported_auth_types": supported_auth_types,
        "candidate_urls": candidate_urls,
        "base_url": base_url,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


def _build_repo() -> SQLitePresetOverrideRepo:
    """Build a repo with a no-op connection (we only call _row_to_override)."""
    db = AsyncMock(spec=Connection)
    return SQLitePresetOverrideRepo(db=db, write_context=make_private_write_context())


class TestRowToOverrideHappy:
    def test_minimal_row_round_trips(self) -> None:
        repo = _build_repo()
        row = _make_row()
        result = repo._row_to_override(row)
        assert result.preset_name == "test-cloud-provider"
        assert result.candidate_urls is None
        assert result.default_models is None
        assert result.supported_auth_types is None

    def test_with_candidate_urls(self) -> None:
        repo = _build_repo()
        row = _make_row(
            candidate_urls='["http://localhost:11434"]',
            base_url=None,
        )
        result = repo._row_to_override(row)
        assert result.candidate_urls == ("http://localhost:11434",)


class TestRowToOverrideCorruption:
    """Every branch that fails closed with ``QueryError``."""

    def test_invalid_json_in_models_column(self) -> None:
        repo = _build_repo()
        row = _make_row(default_models="{not-json")
        with pytest.raises(QueryError, match="corrupt preset override JSON"):
            repo._row_to_override(row)

    def test_non_array_json_in_models_column(self) -> None:
        repo = _build_repo()
        # Object instead of array is a JSON-shape violation.
        row = _make_row(default_models='{"foo": "bar"}')
        with pytest.raises(QueryError, match="not a JSON array"):
            repo._row_to_override(row)

    def test_non_array_json_in_auth_types_column(self) -> None:
        repo = _build_repo()
        row = _make_row(supported_auth_types="42")
        with pytest.raises(QueryError, match="not a JSON array"):
            repo._row_to_override(row)

    def test_non_string_candidate_url_element(self) -> None:
        repo = _build_repo()
        # Numeric element in an otherwise-valid JSON list.
        row = _make_row(candidate_urls="[123, 456]")
        with pytest.raises(QueryError, match="non-string elements"):
            repo._row_to_override(row)

    def test_corrupt_preset_name_none(self) -> None:
        repo = _build_repo()
        row = _make_row(preset_name=None)
        with pytest.raises(QueryError, match="preset_name corrupt"):
            repo._row_to_override(row)

    def test_corrupt_preset_name_empty(self) -> None:
        repo = _build_repo()
        row = _make_row(preset_name="")
        with pytest.raises(QueryError, match="preset_name corrupt"):
            repo._row_to_override(row)

    def test_corrupt_preset_name_non_string(self) -> None:
        repo = _build_repo()
        row = _make_row(preset_name=42)
        with pytest.raises(QueryError, match="preset_name corrupt"):
            repo._row_to_override(row)

    def test_corrupt_base_url_non_string(self) -> None:
        repo = _build_repo()
        row = _make_row(base_url=42)
        with pytest.raises(QueryError, match="base_url"):
            repo._row_to_override(row)

    def test_corrupt_updated_at_non_string(self) -> None:
        repo = _build_repo()
        row = _make_row(updated_at=12345)
        with pytest.raises(QueryError, match="updated_at"):
            repo._row_to_override(row)

    def test_corrupt_updated_by_none(self) -> None:
        """The motivating bug: ``str(None)`` was producing ``"None"``."""
        repo = _build_repo()
        row = _make_row(updated_by=None)
        with pytest.raises(QueryError, match="updated_by"):
            repo._row_to_override(row)

    def test_corrupt_updated_by_empty(self) -> None:
        repo = _build_repo()
        row = _make_row(updated_by="")
        with pytest.raises(QueryError, match="updated_by"):
            repo._row_to_override(row)


class TestUpsertValidation:
    """Repo-side input validation (separate from the service's own)."""

    async def test_upsert_rejects_missing_updated_at(self) -> None:
        from synthorg.api.dto_provider_capabilities import PresetOverride

        repo = _build_repo()
        # Bypass DTO validation by constructing with model_construct
        # so the test exercises the repo-level guard, not the DTO.
        override = PresetOverride.model_construct(
            preset_name="test-provider",
            base_url="https://x",
            candidate_urls=None,
            default_models=None,
            supported_auth_types=None,
            updated_at=None,
            updated_by=None,
        )
        with pytest.raises(QueryError, match="updated_at"):
            await repo.save(override)


class TestGetWrapsCorruptionAsQueryError:
    """``get()`` wraps deserialise failures so callers see a single type."""

    async def test_get_wraps_value_error_as_query_error(self) -> None:
        # Build a repo whose db.execute returns one bad row.
        bad_row = _make_row(updated_by=None)
        cursor = MagicMock()
        cursor.fetchone = AsyncMock(return_value=bad_row)
        db = MagicMock(spec=Connection)
        db.execute = AsyncMock(return_value=cursor)

        repo = SQLitePresetOverrideRepo(
            db=db, write_context=make_private_write_context()
        )
        with pytest.raises(QueryError):
            await repo.get("test-provider")
