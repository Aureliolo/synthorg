"""Unit tests for the pure snapshot deserializer helpers.

The deserializer helpers wrap :class:`pydantic.ValidationError` in
:class:`OntologyError` so callers in the persistence-bound versioning
factories observe a single domain error type regardless of which
backend produced the corrupted snapshot. These tests cover the
wrapping behaviour directly, independent of any backend.
"""

import json
from datetime import UTC, datetime

import pytest

from synthorg.ontology.errors import OntologyError
from synthorg.ontology.models import EntityDefinition, EntitySource, EntityTier
from synthorg.ontology.versioning import (
    _safe_deserialize_snapshot_dict,
    _safe_deserialize_snapshot_json,
)


def _well_formed_entity() -> EntityDefinition:
    """Build a valid :class:`EntityDefinition` for snapshot tests."""
    return EntityDefinition(
        name="WidgetEntity",
        tier=EntityTier.USER,
        source=EntitySource.API,
        definition="A widget for deserializer tests.",
        created_by="tester",
        created_at=datetime(2026, 5, 13, tzinfo=UTC),
        updated_at=datetime(2026, 5, 13, tzinfo=UTC),
    )


@pytest.mark.unit
class TestSafeDeserializeSnapshotJson:
    """JSON-text path of the snapshot deserializer."""

    def test_round_trip_returns_equivalent_model(self) -> None:
        """Valid JSON round-trips to a structurally-equal EntityDefinition."""
        original = _well_formed_entity()

        result = _safe_deserialize_snapshot_json(original.model_dump_json())

        assert result == original

    def test_invalid_json_wraps_validation_error(self) -> None:
        """Invalid JSON surfaces as ``OntologyError`` with chained cause."""
        with pytest.raises(OntologyError) as exc_info:
            _safe_deserialize_snapshot_json("{not valid json}")

        assert exc_info.value.__cause__ is not None
        assert "Corrupted entity definition version snapshot" in str(exc_info.value)

    def test_missing_required_fields_wraps_validation_error(self) -> None:
        """JSON missing required fields surfaces as ``OntologyError``."""
        bad_payload = json.dumps({"name": "Incomplete"})

        with pytest.raises(OntologyError) as exc_info:
            _safe_deserialize_snapshot_json(bad_payload)

        assert exc_info.value.__cause__ is not None


@pytest.mark.unit
class TestSafeDeserializeSnapshotDict:
    """Parsed-dict path of the snapshot deserializer."""

    def test_round_trip_from_model_dump(self) -> None:
        """A model_dump() payload deserializes back to the original."""
        original = _well_formed_entity()

        result = _safe_deserialize_snapshot_dict(original.model_dump(mode="json"))

        assert result == original

    def test_non_mapping_input_wraps_validation_error(self) -> None:
        """Non-mapping input surfaces as ``OntologyError``."""
        with pytest.raises(OntologyError) as exc_info:
            _safe_deserialize_snapshot_dict(["not", "a", "mapping"])

        assert exc_info.value.__cause__ is not None
        assert "Corrupted entity definition version snapshot" in str(exc_info.value)

    def test_missing_required_fields_wraps_validation_error(self) -> None:
        """Dict missing required fields surfaces as ``OntologyError``."""
        with pytest.raises(OntologyError) as exc_info:
            _safe_deserialize_snapshot_dict({"name": "Incomplete"})

        assert exc_info.value.__cause__ is not None
