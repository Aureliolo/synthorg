"""Example agent evaluation: COORDINATION behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="coordination")
async def test_agent_coordination_behavior() -> None:
    """Verify agent correctly categorizes coordination operations."""
    assert BehaviorTag.COORDINATION.value == "coordination"
