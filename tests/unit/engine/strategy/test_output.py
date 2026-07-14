"""Unit tests for strategic output handler."""

import pytest

from synthorg.engine.strategy.lenses import DEFAULT_LENSES, LENS_DEFINITIONS
from synthorg.engine.strategy.output import build_output_instructions
from synthorg.hr.strategy_mode import StrategicOutputMode

from .conftest import make_agent


class TestBuildOutputInstructions:
    """Tests for build_output_instructions."""

    @pytest.mark.unit
    def test_option_expander_mode(self) -> None:
        lenses = tuple(LENS_DEFINITIONS[lens] for lens in DEFAULT_LENSES)
        result = build_output_instructions(
            mode=StrategicOutputMode.OPTION_EXPANDER,
            lenses=lenses,
        )
        assert "ALL viable options" in result
        assert "do not rank or recommend" in result.lower()

    @pytest.mark.unit
    def test_advisor_mode(self) -> None:
        result = build_output_instructions(
            mode=StrategicOutputMode.ADVISOR,
            lenses=(),
        )
        assert "top 2-3" in result.lower()
        assert "advice, not a decision" in result.lower()

    @pytest.mark.unit
    def test_decision_maker_mode(self) -> None:
        result = build_output_instructions(
            mode=StrategicOutputMode.DECISION_MAKER,
            lenses=(),
        )
        assert "make a final recommendation" in result.lower()
        assert "state your decision clearly" in result.lower()

    @pytest.mark.unit
    def test_context_dependent_resolves_for_executive(self) -> None:
        agent = make_agent(role="CEO")
        result = build_output_instructions(
            mode=StrategicOutputMode.CONTEXT_DEPENDENT,
            lenses=(),
            agent=agent,
        )
        # Should resolve to decision_maker for the executive tier.
        assert "state your decision clearly" in result.lower()
        assert "advice, not a decision" not in result.lower()

    @pytest.mark.unit
    def test_context_dependent_resolves_for_ic(self) -> None:
        agent = make_agent(role="Backend Developer", name="Mid")
        result = build_output_instructions(
            mode=StrategicOutputMode.CONTEXT_DEPENDENT,
            lenses=(),
            agent=agent,
        )
        # Should resolve to advisor for a non-executive role.
        assert "advice, not a decision" in result.lower()
        assert "state your decision clearly" not in result.lower()

    @pytest.mark.unit
    def test_lenses_included_in_output(self) -> None:
        lenses = tuple(LENS_DEFINITIONS[lens] for lens in DEFAULT_LENSES)
        result = build_output_instructions(
            mode=StrategicOutputMode.ADVISOR,
            lenses=lenses,
        )
        assert "Contrarian" in result
        assert "Risk-Focused" in result

    @pytest.mark.unit
    def test_all_modes_produce_non_empty_output(self) -> None:
        for mode in StrategicOutputMode:
            if mode == StrategicOutputMode.CONTEXT_DEPENDENT:
                continue  # Needs agent for resolution
            result = build_output_instructions(mode=mode, lenses=())
            assert len(result) > 20
