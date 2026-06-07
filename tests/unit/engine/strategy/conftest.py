"""Shared fixtures for strategy module tests."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.strategy.models import (
    ConfidenceMetadata,
    ProgressiveConfig,
    RiskCard,
    StrategicContext,
    StrategyConfig,
)
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.strategy_mode import StrategicOutputMode


@pytest.fixture
def default_strategy_config() -> StrategyConfig:
    """Default strategy config with all defaults."""
    return StrategyConfig()


@pytest.fixture
def strategic_context() -> StrategicContext:
    """Sample strategic context."""
    return StrategicContext(
        maturity_stage="growth",
        industry="technology",
        competitive_position="challenger",
    )


@pytest.fixture
def risk_card() -> RiskCard:
    """Sample risk card."""
    return RiskCard(decision_type="product_launch")


@pytest.fixture
def progressive_config() -> ProgressiveConfig:
    """Default progressive config."""
    return ProgressiveConfig()


@pytest.fixture
def confidence_metadata() -> ConfidenceMetadata:
    """Sample confidence metadata."""
    return ConfidenceMetadata(
        level=0.75,
        range_lower=0.6,
        range_upper=0.9,
        assumptions=("Market conditions remain stable",),
        uncertainty_factors=("Competitor response unknown",),
    )


def make_agent(
    *,
    level: SeniorityLevel = SeniorityLevel.C_SUITE,
    strategic_output_mode: StrategicOutputMode | None = None,
    name: str = "Test Agent",
    role: str = "Test Role",
) -> AgentIdentity:
    """Create a minimal agent for testing."""
    return AgentIdentity(
        name=name,
        role=role,
        department="executive",
        level=level,
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        strategic_output_mode=strategic_output_mode,
    )
