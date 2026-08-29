"""Tests for lifecycle strategy factory."""

import pytest

from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from synthorg.tools.sandbox.lifecycle.per_task import PerTaskStrategy

pytestmark = pytest.mark.unit


class TestCreateLifecycleStrategy:
    """Factory dispatches to correct strategy implementation."""

    @pytest.mark.parametrize(
        ("strategy", "expected_cls"),
        [
            ("per-agent", PerAgentStrategy),
            ("per-task", PerTaskStrategy),
            ("per-call", PerCallStrategy),
        ],
    )
    def test_valid_strategies(
        self,
        strategy: str,
        expected_cls: type,
    ) -> None:
        config = SandboxLifecycleConfig(strategy=strategy)  # type: ignore[arg-type]
        result = create_lifecycle_strategy(config)
        assert isinstance(result, expected_cls)

    @pytest.mark.parametrize("strategy", ["per-agent", "per-task"])
    async def test_pin_check_reaches_reusable_strategies(self, strategy: str) -> None:
        """A strategy holding a persistent container receives pin_check.

        per-call is deliberately excluded: it destroys its container
        after every call, so there is nothing for a pin to hold open,
        and the factory does not thread pin_check to it at all.
        """

        async def pin_check(_container_id: str) -> bool:
            return False

        config = SandboxLifecycleConfig(strategy=strategy)  # type: ignore[arg-type]
        result = create_lifecycle_strategy(config, pin_check=pin_check)
        assert isinstance(result, PerAgentStrategy | PerTaskStrategy)
        assert result._pin_check is pin_check
