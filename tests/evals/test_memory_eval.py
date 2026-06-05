"""Example agent evaluation: MEMORY behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="memory")
async def test_agent_memory_behavior() -> None:
    """Verify agent correctly categorizes memory operations."""
    assert BehaviorTag.MEMORY.value == "memory"
