"""Unit tests for strategy integration in prompt building."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.prompt import build_system_prompt
from synthorg.engine.prompt_template import PROMPT_TEMPLATE_VERSION
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.strategy_mode import StrategicOutputMode


def _make_agent(
    *,
    level: SeniorityLevel = SeniorityLevel.C_SUITE,
    strategic_output_mode: StrategicOutputMode | None = None,
) -> AgentIdentity:
    return AgentIdentity(
        name="Test CEO",
        role="CEO",
        department="executive",
        level=level,
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        strategic_output_mode=strategic_output_mode,
    )


class TestPromptStrategyIntegration:
    """Tests for strategy sections in system prompt."""

    @pytest.mark.unit
    def test_no_strategy_config_no_strategic_sections(self) -> None:
        agent = _make_agent()
        prompt = build_system_prompt(agent=agent)
        assert "Strategic Analysis Framework" not in prompt.content
        assert "strategy" not in prompt.sections

    @pytest.mark.unit
    def test_strategy_config_adds_sections_for_c_suite(self) -> None:
        agent = _make_agent(level=SeniorityLevel.C_SUITE)
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Strategic Analysis Framework" in prompt.content
        assert "strategy" in prompt.sections

    @pytest.mark.unit
    def test_strategy_config_no_sections_for_mid_level(self) -> None:
        agent = _make_agent(level=SeniorityLevel.MID)
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Strategic Analysis Framework" not in prompt.content
        assert "strategy" not in prompt.sections

    @pytest.mark.unit
    def test_explicit_mode_overrides_level(self) -> None:
        agent = _make_agent(
            level=SeniorityLevel.MID,
            strategic_output_mode=StrategicOutputMode.ADVISOR,
        )
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Strategic Analysis Framework" in prompt.content

    @pytest.mark.unit
    def test_strategic_context_contains_config_values(self) -> None:
        agent = _make_agent()
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "technology" in prompt.content
        assert "growth" in prompt.content
        assert "challenger" in prompt.content

    @pytest.mark.unit
    def test_constitutional_principles_injected(self) -> None:
        agent = _make_agent()
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Constitutional Principles" in prompt.content
        assert "anti-trendslop" in prompt.content.lower()

    @pytest.mark.unit
    def test_contrarian_section_present(self) -> None:
        agent = _make_agent()
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Contrarian Analysis" in prompt.content

    @pytest.mark.unit
    def test_confidence_section_present(self) -> None:
        agent = _make_agent()
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Confidence Calibration" in prompt.content

    @pytest.mark.unit
    def test_assumption_section_present(self) -> None:
        agent = _make_agent()
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Assumption Surfacing" in prompt.content

    @pytest.mark.unit
    def test_output_requirements_present(self) -> None:
        agent = _make_agent()
        config = StrategyConfig()
        prompt = build_system_prompt(agent=agent, strategy_config=config)
        assert "Output Requirements" in prompt.content

    @pytest.mark.unit
    def test_template_version_bumped(self) -> None:
        agent = _make_agent()
        prompt = build_system_prompt(agent=agent)
        assert prompt.template_version == PROMPT_TEMPLATE_VERSION
