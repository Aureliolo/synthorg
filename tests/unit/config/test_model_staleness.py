"""Tests for the per-model staleness marker sub-model."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from synthorg.config.model_staleness import ModelStaleness
from synthorg.config.schema import ProviderModelConfig

_FLAGGED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestModelStaleness:
    def test_minimal_construction(self) -> None:
        stale = ModelStaleness(reason="removed_from_catalog", flagged_at=_FLAGGED)
        assert stale.reason == "removed_from_catalog"
        assert stale.flagged_at == _FLAGGED
        assert stale.last_seen is None
        assert stale.successor_model_id is None

    def test_full_construction(self) -> None:
        stale = ModelStaleness(
            reason="deprecated",
            flagged_at=_FLAGGED,
            last_seen=date(2026, 5, 30),
            successor_model_id="example-large-002",
        )
        assert stale.reason == "deprecated"
        assert stale.last_seen == date(2026, 5, 30)
        assert stale.successor_model_id == "example-large-002"

    def test_frozen(self) -> None:
        stale = ModelStaleness(reason="deprecated", flagged_at=_FLAGGED)
        with pytest.raises(ValidationError):
            stale.reason = "removed_from_catalog"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ModelStaleness(  # type: ignore[call-arg]
                reason="deprecated",
                flagged_at=_FLAGGED,
                bogus=True,
            )

    def test_invalid_reason_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelStaleness(reason="gone", flagged_at=_FLAGGED)  # type: ignore[arg-type]

    def test_blank_successor_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelStaleness(
                reason="deprecated",
                flagged_at=_FLAGGED,
                successor_model_id="   ",
            )


@pytest.mark.unit
class TestProviderModelConfigStaleness:
    def test_stale_defaults_to_none(self) -> None:
        model = ProviderModelConfig(id="example-large-001")
        assert model.stale is None

    def test_legacy_shape_without_stale_validates(self) -> None:
        model = ProviderModelConfig.model_validate({"id": "example-small-001"})
        assert model.stale is None

    def test_stale_round_trips_through_json(self) -> None:
        model = ProviderModelConfig(
            id="example-large-001",
            stale=ModelStaleness(
                reason="removed_from_catalog",
                flagged_at=_FLAGGED,
                successor_model_id="example-large-002",
            ),
        )
        restored = ProviderModelConfig.model_validate(model.model_dump(mode="json"))
        assert restored.stale is not None
        assert restored.stale.reason == "removed_from_catalog"
        assert restored.stale.successor_model_id == "example-large-002"
