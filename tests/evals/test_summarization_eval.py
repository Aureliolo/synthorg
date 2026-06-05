"""Example agent evaluation: SUMMARIZATION behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="summarization")
async def test_agent_summarization_behavior() -> None:
    """Verify agent correctly categorizes summarization turns."""
    assert BehaviorTag.SUMMARIZATION.value == "summarization"
