"""Tests for consolidation configuration."""

import pytest
from pydantic import ValidationError

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.consolidation.config import (
    ArchivalConfig,
    ConsolidationConfig,
    DualModeConfig,
    LLMConsolidationConfig,
    RetentionConfig,
)
from synthorg.memory.consolidation.models import RetentionRule
from synthorg.memory.enums import ConsolidationInterval


@pytest.mark.unit
class TestRetentionConfig:
    """RetentionConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = RetentionConfig()
        assert config.rules == ()
        assert config.default_retention_days is None

    def test_with_rules(self) -> None:
        config = RetentionConfig(
            rules=(
                RetentionRule(category=MemoryCategory.WORKING, retention_days=7),
                RetentionRule(category=MemoryCategory.EPISODIC, retention_days=30),
            ),
            default_retention_days=90,
        )
        assert len(config.rules) == 2
        assert config.default_retention_days == 90


@pytest.mark.unit
class TestArchivalConfig:
    """ArchivalConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = ArchivalConfig()
        assert config.enabled is False
        assert config.age_threshold_days == 90

    def test_enabled(self) -> None:
        config = ArchivalConfig(enabled=True, age_threshold_days=30)
        assert config.enabled is True
        assert config.age_threshold_days == 30

    def test_zero_threshold_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ArchivalConfig(age_threshold_days=0)


@pytest.mark.unit
class TestConsolidationConfig:
    """ConsolidationConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = ConsolidationConfig()
        assert config.interval == ConsolidationInterval.DAILY
        assert config.max_memories_per_agent == 10_000
        assert config.retention.rules == ()
        assert config.archival.enabled is False

    def test_custom(self) -> None:
        config = ConsolidationConfig(
            interval=ConsolidationInterval.HOURLY,
            max_memories_per_agent=500,
        )
        assert config.interval == ConsolidationInterval.HOURLY
        assert config.max_memories_per_agent == 500

    def test_zero_max_memories_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConsolidationConfig(max_memories_per_agent=0)


@pytest.mark.unit
class TestRetentionConfigValidation:
    """RetentionConfig duplicate category validation."""

    def test_duplicate_retention_categories_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate retention categories"):
            RetentionConfig(
                rules=(
                    RetentionRule(
                        category=MemoryCategory.WORKING,
                        retention_days=7,
                    ),
                    RetentionRule(
                        category=MemoryCategory.WORKING,
                        retention_days=30,
                    ),
                ),
            )


@pytest.mark.unit
class TestDualModeConfig:
    """DualModeConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = DualModeConfig()
        assert config.enabled is False
        assert config.dense_threshold == 0.5
        assert config.summarization_model is None
        assert config.max_summary_tokens == 200
        assert config.max_facts == 20
        assert config.anchor_length == 150

    def test_custom(self) -> None:
        config = DualModeConfig(
            enabled=True,
            dense_threshold=0.7,
            summarization_model="test-small-001",
            max_summary_tokens=500,
            max_facts=10,
            anchor_length=200,
        )
        assert config.enabled is True
        assert config.dense_threshold == 0.7
        assert config.summarization_model == "test-small-001"

    def test_threshold_bounds(self) -> None:
        with pytest.raises(ValidationError):
            DualModeConfig(dense_threshold=-0.1)
        with pytest.raises(ValidationError):
            DualModeConfig(dense_threshold=1.1)

    def test_max_summary_tokens_bounds(self) -> None:
        with pytest.raises(ValidationError):
            DualModeConfig(max_summary_tokens=49)
        with pytest.raises(ValidationError):
            DualModeConfig(max_summary_tokens=1001)

    def test_max_facts_bounds(self) -> None:
        with pytest.raises(ValidationError):
            DualModeConfig(max_facts=0)
        with pytest.raises(ValidationError):
            DualModeConfig(max_facts=101)

    def test_anchor_length_bounds(self) -> None:
        with pytest.raises(ValidationError):
            DualModeConfig(anchor_length=49)
        with pytest.raises(ValidationError):
            DualModeConfig(anchor_length=501)

    def test_frozen(self) -> None:
        config = DualModeConfig()
        with pytest.raises(ValidationError):
            config.enabled = True  # type: ignore[misc]

    def test_enabled_requires_model(self) -> None:
        with pytest.raises(
            ValidationError,
            match="summarization_model must be non-blank",
        ):
            DualModeConfig(enabled=True, summarization_model=None)

    def test_enabled_with_blank_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DualModeConfig(enabled=True, summarization_model="   ")

    def test_disabled_allows_none_model(self) -> None:
        config = DualModeConfig(enabled=False, summarization_model=None)
        assert config.summarization_model is None


@pytest.mark.unit
class TestArchivalConfigDualMode:
    """ArchivalConfig with nested DualModeConfig."""

    def test_backward_compatible_default(self) -> None:
        """Existing ArchivalConfig() still works with dual_mode added."""
        config = ArchivalConfig()
        assert config.dual_mode.enabled is False
        assert config.dual_mode.dense_threshold == 0.5

    def test_with_custom_dual_mode(self) -> None:
        config = ArchivalConfig(
            enabled=True,
            dual_mode=DualModeConfig(
                enabled=True,
                dense_threshold=0.8,
                summarization_model="test-small-001",
            ),
        )
        assert config.enabled is True
        assert config.dual_mode.enabled is True
        assert config.dual_mode.dense_threshold == 0.8


@pytest.mark.unit
class TestLLMConsolidationConfig:
    """LLMConsolidationConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = LLMConsolidationConfig()
        assert config.group_threshold == 3
        assert config.temperature == 0.3
        assert config.max_summary_tokens == 500
        assert config.include_distillation_context is True
        assert config.max_trajectory_context_entries == 5
        assert config.max_trajectory_chars_per_entry == 500
        assert config.max_entry_input_chars == 2000
        assert config.max_total_user_content_chars == 20000
        assert config.fallback_truncate_length == 200

    def test_frozen(self) -> None:
        config = LLMConsolidationConfig()
        with pytest.raises(ValidationError):
            config.group_threshold = 5  # type: ignore[misc]

    def test_group_threshold_below_min_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LLMConsolidationConfig(group_threshold=2)

    def test_group_threshold_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LLMConsolidationConfig(group_threshold=1)

    def test_group_threshold_three_accepted(self) -> None:
        config = LLMConsolidationConfig(group_threshold=3)
        assert config.group_threshold == 3

    def test_temperature_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            LLMConsolidationConfig(temperature=3.0)

    def test_max_summary_tokens_below_min(self) -> None:
        with pytest.raises(ValidationError):
            LLMConsolidationConfig(max_summary_tokens=10)

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LLMConsolidationConfig(temperature=float("nan"))

    def test_entry_chars_exceeds_total_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not exceed"):
            LLMConsolidationConfig(
                max_entry_input_chars=20001,
                max_total_user_content_chars=20000,
            )

    def test_entry_chars_equals_total_accepted(self) -> None:
        config = LLMConsolidationConfig(
            max_entry_input_chars=20000,
            max_total_user_content_chars=20000,
        )
        assert config.max_entry_input_chars == 20000
        assert config.max_total_user_content_chars == 20000

    def test_custom_values(self) -> None:
        config = LLMConsolidationConfig(
            group_threshold=5,
            temperature=0.7,
            max_summary_tokens=1000,
            include_distillation_context=False,
            max_entry_input_chars=500,
        )
        assert config.group_threshold == 5
        assert config.temperature == 0.7
        assert config.max_summary_tokens == 1000
        assert config.include_distillation_context is False
        assert config.max_entry_input_chars == 500
