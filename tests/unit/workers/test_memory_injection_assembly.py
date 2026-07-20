"""Tests for the memory collaborators threaded into the boot AgentEngine.

The regression these guard is the defining one: ``_construct_agent_engine``
passed ``memory_backend`` but never ``memory_injection_strategy``, so
``AgentEngine._retrieve_injected_memory_messages`` short-circuited on
every task and no agent ever received a memory it had not explicitly
asked for.
"""

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import synthorg.api.lifecycle_assembly
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.state import MemoryStateSlice
from synthorg.workers._memory_assembly import (
    build_memory_injection_strategy_or_none,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


class TestMemoryInjectionStrategyAssembly:
    """A wired backend must yield a wired injection strategy."""

    def test_strategy_is_built_when_a_backend_is_wired(self) -> None:
        app_state = make_app_state(memory_backend=MagicMock(spec=MemoryBackend))

        strategy = build_memory_injection_strategy_or_none(app_state)

        assert strategy is not None
        assert isinstance(strategy, MemoryInjectionStrategy)

    def test_no_strategy_without_a_backend(self) -> None:
        # Not a silent degrade: with no backend there is nothing to inject,
        # and the engine keeps its existing no-injection behaviour rather
        # than constructing a strategy over nothing.
        app_state = make_app_state()

        assert build_memory_injection_strategy_or_none(app_state) is None

    def test_strategy_is_bound_to_the_wired_backend(self) -> None:
        backend = MagicMock(spec=MemoryBackend)
        app_state = make_app_state(memory_backend=backend)

        strategy = build_memory_injection_strategy_or_none(app_state)

        assert app_state.slice(MemoryStateSlice).backend is backend
        assert strategy is not None


class TestOrgMemoryWiringOrder:
    """The org backend must exist before runtime services read it.

    ``wire_org_memory_backend`` used to run inside ``_wire_features``,
    which is ordered after ``_install_runtime_services``. Every consumer
    that asks for the org layer does so while assembling an agent, so it
    read ``None`` and the whole layer stayed unreachable for the life of
    the process.
    """

    def test_org_wiring_precedes_runtime_services(self) -> None:
        source = Path(inspect.getfile(synthorg.api.lifecycle_assembly)).read_text(
            encoding="utf-8"
        )
        hooks = source.split("startup = [", 1)[1].split("]", 1)[0]

        memory_at = hooks.index("_wire_memory_backend")
        runtime_at = hooks.index("_install_runtime_services")

        assert memory_at < runtime_at

    def test_org_backend_is_wired_by_the_memory_hook(self) -> None:
        source = Path(inspect.getfile(synthorg.api.lifecycle_assembly)).read_text(
            encoding="utf-8"
        )
        hook = source.split("async def _wire_memory_backend", 1)[1].split(
            "async def ", 1
        )[0]

        assert "wire_org_memory_backend" in hook
