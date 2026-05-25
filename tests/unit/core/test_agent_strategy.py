"""Unit tests for AgentIdentity strategic_output_mode field."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import StrategicOutputMode


def _make_agent(**kwargs: Any) -> AgentIdentity:
    defaults: dict[str, Any] = {
        "name": "Test Agent",
        "role": "CEO",
        "department": "executive",
        "model": ModelConfig(provider="test-provider", model_id="test-model-001"),
        "hiring_date": date(2026, 1, 1),
    }
    defaults.update(kwargs)
    return AgentIdentity(**defaults)


class TestAgentStrategicOutputMode:
    """Tests for the strategic_output_mode field on AgentIdentity."""

    @pytest.mark.unit
    def test_default_is_none(self) -> None:
        agent = _make_agent()
        assert agent.strategic_output_mode is None

    @pytest.mark.unit
    @pytest.mark.parametrize("mode", list(StrategicOutputMode))
    def test_all_modes_accepted(self, mode: StrategicOutputMode) -> None:
        agent = _make_agent(strategic_output_mode=mode)
        assert agent.strategic_output_mode == mode

    @pytest.mark.unit
    def test_mode_from_string(self) -> None:
        agent = _make_agent(strategic_output_mode="advisor")
        assert agent.strategic_output_mode == StrategicOutputMode.ADVISOR

    @pytest.mark.unit
    def test_frozen(self) -> None:
        agent = _make_agent()
        with pytest.raises(ValidationError):
            agent.strategic_output_mode = StrategicOutputMode.ADVISOR  # type: ignore[misc]
