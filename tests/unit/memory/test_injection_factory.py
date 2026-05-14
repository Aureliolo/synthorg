"""Tests for build_memory_injection_strategy dispatch.

The three concrete impls already have dedicated test suites (see
``test_retriever.py``, ``test_tool_retriever.py``, and
``test_self_editing.py``); this module only verifies that the new
factory picks the right one for each :class:`InjectionStrategy` enum
value, and that each impl satisfies the runtime-checkable Protocol.
"""

import pytest

from synthorg.memory.injection import (
    InjectionStrategy,
    MemoryInjectionStrategy,
)
from synthorg.memory.injection_factory import build_memory_injection_strategy
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.self_editing import (
    SelfEditingMemoryConfig,
    SelfEditingMemoryStrategy,
)
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from tests._shared import mock_of


@pytest.mark.unit
class TestBuildMemoryInjectionStrategy:
    """``build_memory_injection_strategy`` dispatches by config.strategy."""

    def test_context_strategy(self) -> None:
        config = MemoryRetrievalConfig(strategy=InjectionStrategy.CONTEXT)
        strategy = build_memory_injection_strategy(
            config,
            backend=mock_of[MemoryBackend](),
        )
        assert isinstance(strategy, ContextInjectionStrategy)

    def test_tool_based_strategy(self) -> None:
        config = MemoryRetrievalConfig(strategy=InjectionStrategy.TOOL_BASED)
        strategy = build_memory_injection_strategy(
            config,
            backend=mock_of[MemoryBackend](),
        )
        assert isinstance(strategy, ToolBasedInjectionStrategy)

    def test_self_editing_strategy(self) -> None:
        config = MemoryRetrievalConfig(strategy=InjectionStrategy.SELF_EDITING)
        strategy = build_memory_injection_strategy(
            config,
            backend=mock_of[MemoryBackend](),
            self_editing_config=SelfEditingMemoryConfig(),
        )
        assert isinstance(strategy, SelfEditingMemoryStrategy)


@pytest.mark.unit
class TestMemoryInjectionStrategyConformance:
    """Every dispatched impl satisfies the @runtime_checkable Protocol."""

    @pytest.mark.parametrize(
        "kind",
        list(InjectionStrategy),
    )
    def test_all_impls_satisfy_protocol(
        self,
        kind: InjectionStrategy,
    ) -> None:
        config = MemoryRetrievalConfig(strategy=kind)
        strategy = build_memory_injection_strategy(
            config,
            backend=mock_of[MemoryBackend](),
            self_editing_config=(
                SelfEditingMemoryConfig()
                if kind is InjectionStrategy.SELF_EDITING
                else None
            ),
        )
        assert isinstance(strategy, MemoryInjectionStrategy)
