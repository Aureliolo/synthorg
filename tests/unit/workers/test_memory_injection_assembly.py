"""Tests for the memory collaborators threaded into the boot AgentEngine.

The regression these guard is the defining one: ``_construct_agent_engine``
passed ``memory_backend`` but never ``memory_injection_strategy``, so
``AgentEngine._retrieve_injected_memory_messages`` short-circuited on
every task and no agent ever received a memory it had not explicitly
asked for.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.state import MemoryStateSlice
from synthorg.workers._engine_assembly import _build_memory_injection_strategy
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


class TestMemoryInjectionStrategyAssembly:
    """A wired backend must yield a wired injection strategy."""

    def test_strategy_is_built_when_a_backend_is_wired(self) -> None:
        app_state = make_app_state(memory_backend=MagicMock(spec=MemoryBackend))

        strategy = _build_memory_injection_strategy(app_state)

        assert strategy is not None
        assert isinstance(strategy, MemoryInjectionStrategy)

    def test_no_strategy_without_a_backend(self) -> None:
        # Not a silent degrade: with no backend there is nothing to inject,
        # and the engine keeps its existing no-injection behaviour rather
        # than constructing a strategy over nothing.
        app_state = make_app_state()

        assert _build_memory_injection_strategy(app_state) is None

    def test_strategy_is_bound_to_the_wired_backend(self) -> None:
        backend = MagicMock(spec=MemoryBackend)
        app_state = make_app_state(memory_backend=backend)

        strategy = _build_memory_injection_strategy(app_state)

        assert app_state.slice(MemoryStateSlice).backend is backend
        assert strategy is not None
