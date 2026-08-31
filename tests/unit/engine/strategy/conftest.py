"""Shared fixtures for strategy module tests."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.hr.strategy_mode import StrategicOutputMode


@pytest.fixture
def default_strategy_config() -> StrategyConfig:
    """Default strategy config with all defaults."""
    return StrategyConfig()


def make_agent(
    *,
    strategic_output_mode: StrategicOutputMode | None = None,
    name: str = "Test Agent",
    role: str = "CEO",
) -> AgentIdentity:
    """Create a minimal agent for testing.

    Defaults to the ``CEO`` role (executive tier, reporting depth 0) so
    strategic/decision-maker paths gated on reporting depth fire.
    """
    return AgentIdentity(
        name=name,
        role=role,
        department="executive",
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        strategic_output_mode=strategic_output_mode,
    )
