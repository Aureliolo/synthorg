"""Unit tests for strategy prompt injection."""

import pytest

from synthorg.engine.strategy.models import StrategyConfig
from synthorg.engine.strategy.prompt_injection import (
    build_strategic_prompt_sections,
    should_inject_strategy,
)
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.strategy_mode import StrategicOutputMode

from .conftest import make_agent


class TestShouldInjectStrategy:
    """Tests for should_inject_strategy."""

    @pytest.mark.unit
    def test_none_config_returns_false(self) -> None:
        agent = make_agent()
        assert should_inject_strategy(agent, None) is False

    @pytest.mark.unit
    def test_c_suite_returns_true(self) -> None:
        agent = make_agent(level=SeniorityLevel.C_SUITE)
        config = StrategyConfig()
        assert should_inject_strategy(agent, config) is True

    @pytest.mark.unit
    def test_vp_returns_true(self) -> None:
        agent = make_agent(level=SeniorityLevel.VP)
        config = StrategyConfig()
        assert should_inject_strategy(agent, config) is True

    @pytest.mark.unit
    def test_director_returns_true(self) -> None:
        agent = make_agent(level=SeniorityLevel.DIRECTOR)
        config = StrategyConfig()
        assert should_inject_strategy(agent, config) is True

    @pytest.mark.unit
    def test_mid_returns_false(self) -> None:
        agent = make_agent(level=SeniorityLevel.MID)
        config = StrategyConfig()
        assert should_inject_strategy(agent, config) is False

    @pytest.mark.unit
    def test_explicit_mode_overrides_level(self) -> None:
        agent = make_agent(
            level=SeniorityLevel.MID,
            strategic_output_mode=StrategicOutputMode.ADVISOR,
        )
        config = StrategyConfig()
        assert should_inject_strategy(agent, config) is True


class TestBuildStrategicPromptSections:
    """Tests for build_strategic_prompt_sections."""

    @pytest.mark.unit
    def test_returns_all_sections(self) -> None:
        agent = make_agent()
        config = StrategyConfig()
        sections = build_strategic_prompt_sections(
            config=config,
            agent=agent,
        )
        assert "strategic_context_text" in sections
        assert "constitutional_principles_text" in sections
        assert "contrarian_text" in sections
        assert "confidence_text" in sections
        assert "assumption_text" in sections
        assert "output_instructions_text" in sections

    @pytest.mark.unit
    def test_context_text_contains_config_values(self) -> None:
        agent = make_agent()
        config = StrategyConfig()
        sections = build_strategic_prompt_sections(
            config=config,
            agent=agent,
        )
        text = sections["strategic_context_text"]
        assert text is not None
        assert "technology" in text
        assert "growth" in text
        assert "challenger" in text

    @pytest.mark.unit
    def test_principles_none_when_empty(self) -> None:
        agent = make_agent()
        config = StrategyConfig()
        sections = build_strategic_prompt_sections(
            config=config,
            agent=agent,
            principles=(),
        )
        assert sections["constitutional_principles_text"] is None

    @pytest.mark.unit
    def test_principles_text_when_provided(self) -> None:
        from synthorg.engine.strategy.models import ConstitutionalPrinciple

        agent = make_agent()
        config = StrategyConfig()
        principles = (ConstitutionalPrinciple(id="test", text="Test rule text"),)
        sections = build_strategic_prompt_sections(
            config=config,
            agent=agent,
            principles=principles,
        )
        text = sections["constitutional_principles_text"]
        assert text is not None
        assert "Test rule text" in text

    @pytest.mark.unit
    def test_output_instructions_non_empty(self) -> None:
        agent = make_agent()
        config = StrategyConfig()
        sections = build_strategic_prompt_sections(
            config=config,
            agent=agent,
        )
        assert sections["output_instructions_text"]
        assert len(sections["output_instructions_text"]) > 20
