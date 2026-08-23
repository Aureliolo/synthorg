"""Tests for the multi-agent conversation response model."""

import pytest
from pydantic import ValidationError

from synthorg.communication.multi_agent import AgentResponse

pytestmark = pytest.mark.unit


class TestAgentResponse:
    """The invariants a turn's result carries to its consumers."""

    def test_defaults_are_a_silent_free_turn(self) -> None:
        response = AgentResponse(agent_id="agent-1", content="")
        assert response.input_tokens == 0
        assert response.output_tokens == 0
        assert response.cost == pytest.approx(0.0)

    def test_is_frozen(self) -> None:
        response = AgentResponse(agent_id="agent-1", content="hello")
        with pytest.raises(ValidationError):
            response.content = "rewritten"  # type: ignore[misc]

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            AgentResponse(agent_id="agent-1", content="x", speaker="agent-2")  # type: ignore[call-arg]

    def test_rejects_a_blank_agent_id(self) -> None:
        with pytest.raises(ValidationError):
            AgentResponse(agent_id="   ", content="x")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("input_tokens", -1),
            ("output_tokens", -1),
            ("cost", -0.01),
        ],
    )
    def test_rejects_negative_usage(self, field: str, value: float) -> None:
        """A negative tally would subtract from a conversation's spend."""
        usage: dict[str, object] = {field: value}
        with pytest.raises(ValidationError):
            AgentResponse.model_validate(
                {"agent_id": "agent-1", "content": "x", **usage},
            )

    @pytest.mark.parametrize("value", [float("inf"), float("nan")])
    def test_rejects_non_finite_cost(self, value: float) -> None:
        with pytest.raises(ValidationError):
            AgentResponse(agent_id="agent-1", content="x", cost=value)
