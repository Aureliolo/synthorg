"""Unit tests for promotion configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import SeniorityLevel
from synthorg.hr.promotion.config import (
    ModelMappingConfig,
    PromotionApprovalConfig,
    PromotionConfig,
    PromotionCriteriaConfig,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.mark.unit
class TestPromotionConfig:
    """Tests for PromotionConfig defaults and validation."""

    def test_defaults(self) -> None:
        """PromotionConfig has expected defaults."""
        config = PromotionConfig()
        assert config.enabled is True
        assert config.cooldown_hours == 24
        assert isinstance(config.criteria, PromotionCriteriaConfig)
        assert isinstance(config.approval, PromotionApprovalConfig)
        assert isinstance(config.model_mapping, ModelMappingConfig)

    def test_cooldown_hours_zero_allowed(self) -> None:
        """Cooldown hours can be zero (no cooldown)."""
        config = PromotionConfig(cooldown_hours=0)
        assert config.cooldown_hours == 0

    def test_cooldown_hours_negative_rejected(self) -> None:
        """Negative cooldown hours are rejected."""
        with pytest.raises(ValidationError):
            PromotionConfig(cooldown_hours=-1)

    def test_frozen(self) -> None:
        """PromotionConfig is immutable."""
        config = PromotionConfig()
        with pytest.raises(ValidationError):
            config.enabled = False  # type: ignore[misc]


@pytest.mark.unit
class TestPromotionCriteriaConfig:
    """Tests for PromotionCriteriaConfig defaults and validation."""

    def test_defaults(self) -> None:
        """PromotionCriteriaConfig has expected defaults."""
        config = PromotionCriteriaConfig()
        assert config.min_criteria_met == 2
        assert config.required_criteria == ()

    def test_min_criteria_met_must_be_positive(self) -> None:
        """min_criteria_met must be at least 1."""
        with pytest.raises(ValidationError):
            PromotionCriteriaConfig(min_criteria_met=0)

    def test_custom_required_criteria(self) -> None:
        """required_criteria can be set."""
        config = PromotionCriteriaConfig(
            required_criteria=("quality_score", "success_rate"),
        )
        assert len(config.required_criteria) == 2


@pytest.mark.unit
class TestPromotionApprovalConfig:
    """Tests for PromotionApprovalConfig defaults and validation."""

    def test_defaults(self) -> None:
        """PromotionApprovalConfig has expected defaults."""
        config = PromotionApprovalConfig()
        assert config.human_approval_from_level == SeniorityLevel.SENIOR
        assert config.auto_demote_cost_saving is True
        assert config.human_demote_authority is True

    def test_custom_threshold(self) -> None:
        """human_approval_from_level can be overridden."""
        config = PromotionApprovalConfig(
            human_approval_from_level=SeniorityLevel.LEAD,
        )
        assert config.human_approval_from_level == SeniorityLevel.LEAD


@pytest.mark.unit
class TestModelMappingConfig:
    """Tests for ModelMappingConfig defaults and validation."""

    def test_defaults(self) -> None:
        """ModelMappingConfig has expected defaults."""
        config = ModelMappingConfig()
        assert config.model_follows_seniority is True
        assert config.seniority_model_map == {}

    def test_model_follows_seniority_false(self) -> None:
        """model_follows_seniority can be disabled."""
        config = ModelMappingConfig(model_follows_seniority=False)
        assert config.model_follows_seniority is False

    def test_explicit_model_map(self) -> None:
        """seniority_model_map can be populated."""
        config = ModelMappingConfig(
            seniority_model_map={
                "senior": "test-large-001",
                "lead": "test-large-002",
            },
        )
        assert config.seniority_model_map["senior"] == "test-large-001"
        assert config.seniority_model_map["lead"] == "test-large-002"

    def test_nonexistent_level_in_model_map_raises(self) -> None:
        """Unknown seniority level key in seniority_model_map raises."""
        with pytest.raises(ValueError, match="Unknown seniority level"):
            ModelMappingConfig(
                seniority_model_map={"nonexistent_level": "model-x"},
            )


@pytest.mark.unit
class TestPromotionCriteriaConfigFrozen:
    """Tests for PromotionCriteriaConfig immutability."""

    def test_frozen(self) -> None:
        """PromotionCriteriaConfig is immutable."""
        config = PromotionCriteriaConfig()
        with pytest.raises(ValidationError):
            config.min_criteria_met = 5  # type: ignore[misc]


@pytest.mark.unit
class TestPromotionApprovalConfigFrozen:
    """Tests for PromotionApprovalConfig immutability."""

    def test_frozen(self) -> None:
        """PromotionApprovalConfig is immutable."""
        config = PromotionApprovalConfig()
        with pytest.raises(ValidationError):
            config.auto_demote_cost_saving = False  # type: ignore[misc]


@pytest.mark.unit
class TestModelMappingConfigFrozen:
    """Tests for ModelMappingConfig immutability."""

    def test_frozen(self) -> None:
        """ModelMappingConfig is immutable."""
        config = ModelMappingConfig()
        with pytest.raises(ValidationError):
            config.model_follows_seniority = False  # type: ignore[misc]
