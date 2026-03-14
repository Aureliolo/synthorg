"""Tests for consolidation configuration."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ConsolidationInterval, MemoryCategory
from synthorg.memory.consolidation.config import (
    ArchivalConfig,
    ConsolidationConfig,
    RetentionConfig,
)
from synthorg.memory.consolidation.models import RetentionRule

pytestmark = pytest.mark.timeout(30)


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
