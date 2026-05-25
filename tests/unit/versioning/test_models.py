"""Tests for the generic VersionSnapshot model."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.versioning.models import VersionSnapshot

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)


class _Simple(BaseModel):
    """Minimal Pydantic model used as a stand-in for real entity types."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: int


def _make_snapshot(**overrides: object) -> VersionSnapshot[_Simple]:
    """Build a minimal valid VersionSnapshot[_Simple]."""
    defaults: dict[str, object] = {
        "entity_id": "ent-001",
        "version": 1,
        "content_hash": "a" * 64,
        "snapshot": _Simple(name="test", value=42),
        "saved_by": "system",
        "saved_at": _NOW,
    }
    defaults.update(overrides)
    return VersionSnapshot.model_validate(defaults)


class TestVersionSnapshotCreation:
    """Basic creation and field access."""

    @pytest.mark.unit
    def test_valid_snapshot(self) -> None:
        s = _make_snapshot()
        assert s.entity_id == "ent-001"
        assert s.version == 1
        assert s.content_hash == "a" * 64
        assert s.snapshot.name == "test"
        assert s.snapshot.value == 42
        assert s.saved_by == "system"
        assert s.saved_at == _NOW

    @pytest.mark.unit
    def test_frozen(self) -> None:
        s = _make_snapshot()
        with pytest.raises(ValidationError, match="frozen"):
            s.version = 2  # type: ignore[misc]

    @pytest.mark.unit
    def test_snapshot_field_is_frozen_entity(self) -> None:
        s = _make_snapshot()
        with pytest.raises(ValidationError, match="frozen"):
            s.snapshot.name = "changed"  # type: ignore[misc]


class TestVersionSnapshotValidation:
    """Field constraint enforcement."""

    @pytest.mark.unit
    def test_version_must_be_ge_one(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            _make_snapshot(version=0)

    @pytest.mark.unit
    def test_blank_entity_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_snapshot(entity_id="")

    @pytest.mark.unit
    def test_whitespace_entity_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_snapshot(entity_id="   ")

    @pytest.mark.unit
    def test_blank_content_hash_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_snapshot(content_hash="")

    @pytest.mark.unit
    def test_malformed_content_hash_rejected(self) -> None:
        with pytest.raises(ValidationError, match="64-character lowercase hex"):
            _make_snapshot(content_hash="not-a-sha256-hash")

    @pytest.mark.unit
    def test_blank_saved_by_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_snapshot(saved_by="")

    @pytest.mark.unit
    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_snapshot(saved_at=datetime(2026, 4, 7, 12, 0))  # noqa: DTZ001

    @pytest.mark.unit
    def test_non_utc_aware_datetime_rejected(self) -> None:
        from datetime import timezone as tz

        non_utc = datetime(2026, 4, 7, 12, 0, tzinfo=tz(timedelta(hours=5)))
        with pytest.raises(ValidationError, match="must be UTC"):
            _make_snapshot(saved_at=non_utc)

    @pytest.mark.unit
    def test_version_two_accepted(self) -> None:
        s = _make_snapshot(version=2)
        assert s.version == 2
