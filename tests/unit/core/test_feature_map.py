"""Tests for the FeatureMap / FeatureIndex value objects.

Covers the frozen + extra-forbid + JSON round-trip contracts and the
duplicate-name rejection that guarantees a deterministic index shape.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.feature_map import FeatureIndex, FeatureMap

pytestmark = pytest.mark.unit


def _minimal_feature(name: str = "charter") -> FeatureMap:
    """Return a minimal valid :class:`FeatureMap` for *name*."""
    return FeatureMap(name=name, directory=f"src/synthorg/meta/{name}")


def _minimal_index() -> FeatureIndex:
    """Return a minimal valid :class:`FeatureIndex` with one feature."""
    return FeatureIndex(
        schema_version=1,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        features=(_minimal_feature(),),
    )


class TestFeatureMap:
    """Frozen, extra-forbid, JSON round-trip contract."""

    def test_minimal_construction(self) -> None:
        feature = _minimal_feature()
        assert feature.name == "charter"
        assert feature.directory == "src/synthorg/meta/charter"
        assert feature.settings_namespace is None
        assert feature.protocol_exports == ()
        assert feature.controllers == ()
        assert feature.mcp_tool_names == ()
        assert feature.ghost_wired_symbols == ()
        assert feature.state_slice_fields == ()
        assert feature.depends_on == ()

    def test_full_construction(self) -> None:
        feature = FeatureMap(
            name="charter",
            directory="src/synthorg/meta/charter",
            settings_namespace="charter",
            protocol_exports=("CharterInterviewStrategy",),
            controllers=("CharterController",),
            mcp_tool_names=("synthorg_charter_interview",),
            ghost_wired_symbols=("CharterInterviewService",),
            state_slice_fields=("interview_service",),
            depends_on=("approval",),
        )
        assert feature.settings_namespace == "charter"
        assert feature.protocol_exports == ("CharterInterviewStrategy",)
        assert feature.depends_on == ("approval",)

    def test_is_frozen(self) -> None:
        feature = _minimal_feature()
        with pytest.raises(ValidationError, match="frozen"):
            feature.name = "other"  # type: ignore[misc]

    def test_forbids_extra(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            FeatureMap(
                name="x",
                directory="src/synthorg/x",
                surprise=1,  # type: ignore[call-arg]
            )

    def test_rejects_blank_name(self) -> None:
        with pytest.raises(ValidationError):
            FeatureMap(name="", directory="src/synthorg/x")

    def test_rejects_blank_directory(self) -> None:
        with pytest.raises(ValidationError):
            FeatureMap(name="x", directory="")

    def test_json_round_trip(self) -> None:
        original = FeatureMap(
            name="charter",
            directory="src/synthorg/meta/charter",
            settings_namespace="charter",
            controllers=("CharterController",),
            ghost_wired_symbols=("CharterInterviewService",),
        )
        restored = FeatureMap.model_validate_json(original.model_dump_json())
        assert restored == original


class TestFeatureIndex:
    """Schema version + generated_at + per-feature aggregate contract."""

    def test_minimal_construction(self) -> None:
        index = _minimal_index()
        assert index.schema_version == 1
        assert index.generated_at.tzinfo is UTC
        assert len(index.features) == 1

    def test_is_frozen(self) -> None:
        index = _minimal_index()
        with pytest.raises(ValidationError, match="frozen"):
            index.schema_version = 2  # type: ignore[misc]

    def test_forbids_extra(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            FeatureIndex(
                schema_version=1,
                generated_at=datetime(2026, 5, 28, tzinfo=UTC),
                features=(),
                surprise=1,  # type: ignore[call-arg]
            )

    def test_empty_features_permitted(self) -> None:
        index = FeatureIndex(
            schema_version=1,
            generated_at=datetime(2026, 5, 28, tzinfo=UTC),
            features=(),
        )
        assert index.features == ()

    def test_rejects_duplicate_feature_names(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            FeatureIndex(
                schema_version=1,
                generated_at=datetime(2026, 5, 28, tzinfo=UTC),
                features=(_minimal_feature("dup"), _minimal_feature("dup")),
            )

    def test_distinct_feature_names_accepted(self) -> None:
        index = FeatureIndex(
            schema_version=1,
            generated_at=datetime(2026, 5, 28, tzinfo=UTC),
            features=(_minimal_feature("a"), _minimal_feature("b")),
        )
        assert tuple(f.name for f in index.features) == ("a", "b")

    def test_rejects_naive_generated_at(self) -> None:
        with pytest.raises(ValidationError):
            FeatureIndex(
                schema_version=1,
                generated_at=datetime(2026, 5, 28),  # noqa: DTZ001
                features=(),
            )

    def test_json_round_trip(self) -> None:
        original = _minimal_index()
        restored = FeatureIndex.model_validate_json(original.model_dump_json())
        assert restored == original
