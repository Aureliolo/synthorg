"""Example agent evaluation: DELEGATION behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="delegation")
async def test_agent_delegation_behavior() -> None:
    """Verify agent correctly categorizes delegation operations."""
    assert BehaviorTag.DELEGATION.value == "delegation"
