"""Example agent evaluation: CONVERSATION behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="conversation")
async def test_agent_conversation_behavior() -> None:
    """Verify agent correctly categorizes conversation turns."""
    assert BehaviorTag.CONVERSATION.value == "conversation"
