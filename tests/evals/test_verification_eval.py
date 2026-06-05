"""Example agent evaluation: VERIFICATION behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="verification")
async def test_agent_verification_behavior() -> None:
    """Verify agent correctly categorizes verification operations."""
    assert BehaviorTag.VERIFICATION.value == "verification"
