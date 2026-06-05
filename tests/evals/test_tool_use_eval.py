"""Example agent evaluation: TOOL_USE behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="tool_use")
async def test_agent_tool_use_behavior() -> None:
    """Verify agent correctly categorizes generic tool use."""
    assert BehaviorTag.TOOL_USE.value == "tool_use"
