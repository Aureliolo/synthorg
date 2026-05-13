"""Regression: AgentEngine accepts a Clock parameter and uses it for timing.

Pins the seam so a future refactor that drops the Clock injection
fails immediately. The test does not run a full agent execution; it
asserts the constructor wires ``self._clock`` from the injected
``Clock`` (and falls back to ``SystemClock`` otherwise) and that the
attribute is the same instance passed in.
"""

import pytest

from synthorg.core.clock import SystemClock
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class TestAgentEngineClockSeam:
    def test_default_clock_is_system_clock(self) -> None:
        # Construct a bare AgentEngine via a minimal mock provider.
        from synthorg.engine.agent_engine import AgentEngine
        from synthorg.providers.protocol import CompletionProvider
        from tests._shared.mock_of import mock_of

        provider = mock_of[CompletionProvider]()
        engine = AgentEngine(provider=provider)
        assert isinstance(engine._clock, SystemClock)

    def test_injected_clock_is_used(self) -> None:
        from synthorg.engine.agent_engine import AgentEngine
        from synthorg.providers.protocol import CompletionProvider
        from tests._shared.mock_of import mock_of

        provider = mock_of[CompletionProvider]()
        fake = FakeClock()
        engine = AgentEngine(provider=provider, clock=fake)
        assert engine._clock is fake
