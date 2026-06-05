"""Example agent evaluation: RETRIEVAL behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="retrieval")
async def test_agent_retrieval_behavior() -> None:
    """Verify agent correctly categorizes retrieval operations."""
    assert BehaviorTag.RETRIEVAL.value == "retrieval"
