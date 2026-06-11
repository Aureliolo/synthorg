"""Tests for PTE approximation."""

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.trajectory.pte import (
    PTEConfig,
    compute_trajectory_pte,
    prefill_token_equivalents,
)
from synthorg.execution.turn import TurnRecord


def _make_turn(
    input_tokens: int = 100,
    output_tokens: int = 50,
    prior_tool_call_count: int = 0,
    tool_response_tokens: int = 0,
) -> TurnRecord:
    return TurnRecord(
        turn_number=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.01,
        finish_reason=FinishReason.STOP,
        prior_tool_call_count=prior_tool_call_count,
        tool_response_tokens=tool_response_tokens,
    )


@pytest.mark.unit
class TestPTEConfig:
    """PTEConfig model validation."""

    def test_defaults(self) -> None:
        config = PTEConfig()
        assert config.eviction_penalty == 0.3
        assert config.tool_inflation_factor == 1.5

    def test_custom_values(self) -> None:
        config = PTEConfig(eviction_penalty=0.5, tool_inflation_factor=2.0)
        assert config.eviction_penalty == 0.5
        assert config.tool_inflation_factor == 2.0


@pytest.mark.unit
class TestPrefillTokenEquivalents:
    """prefill_token_equivalents function."""

    def test_no_tools(self) -> None:
        turn = _make_turn(input_tokens=100, output_tokens=50)
        pte = prefill_token_equivalents(turn)
        # PTE = 100 * (1 + 0.3 * 0) + 50 + 0 * 1.5 = 150.0
        assert pte == pytest.approx(150.0)

    def test_with_prior_tool_calls(self) -> None:
        turn = _make_turn(
            input_tokens=100,
            output_tokens=50,
            prior_tool_call_count=3,
        )
        pte = prefill_token_equivalents(turn)
        # PTE = 100 * (1 + 0.3 * 3) + 50 + 0 = 100 * 1.9 + 50 = 240.0
        assert pte == pytest.approx(240.0)

    def test_with_tool_response_tokens(self) -> None:
        turn = _make_turn(
            input_tokens=100,
            output_tokens=50,
            tool_response_tokens=200,
        )
        pte = prefill_token_equivalents(turn)
        # PTE = 100 * 1.0 + 50 + 200 * 1.5 = 450.0
        assert pte == pytest.approx(450.0)

    def test_full_computation(self) -> None:
        turn = _make_turn(
            input_tokens=100,
            output_tokens=50,
            prior_tool_call_count=2,
            tool_response_tokens=100,
        )
        pte = prefill_token_equivalents(turn)
        # PTE = 100 * (1 + 0.3 * 2) + 50 + 100 * 1.5
        # = 100 * 1.6 + 50 + 150 = 360.0
        assert pte == pytest.approx(360.0)

    def test_custom_config(self) -> None:
        config = PTEConfig(eviction_penalty=0.5, tool_inflation_factor=2.0)
        turn = _make_turn(
            input_tokens=100,
            output_tokens=50,
            prior_tool_call_count=1,
            tool_response_tokens=100,
        )
        pte = prefill_token_equivalents(turn, config=config)
        # PTE = 100 * (1 + 0.5 * 1) + 50 + 100 * 2.0
        # = 150 + 50 + 200 = 400.0
        assert pte == pytest.approx(400.0)


@pytest.mark.unit
class TestComputeTrajectoryPTE:
    """compute_trajectory_pte function."""

    def test_empty_turns(self) -> None:
        assert compute_trajectory_pte(()) == 0.0

    def test_single_turn(self) -> None:
        turn = _make_turn(input_tokens=100, output_tokens=50)
        total = compute_trajectory_pte((turn,))
        assert total == pytest.approx(150.0)

    def test_multiple_turns(self) -> None:
        turns = (
            _make_turn(input_tokens=100, output_tokens=50),
            _make_turn(input_tokens=200, output_tokens=100),
        )
        total = compute_trajectory_pte(turns)
        # Turn 1: 150.0, Turn 2: 300.0
        assert total == pytest.approx(450.0)
