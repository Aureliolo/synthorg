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
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.consolidation.wiki_export import WikiExporter
from synthorg.memory.injection import InjectionStrategy, MemoryInjectionStrategy
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.state import MemoryStateSlice
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from synthorg.providers.protocol import CompletionProvider
from synthorg.workers._memory_assembly import (
    build_memory_injection_strategy_or_none,
    wiki_exporter_or_none,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _provider() -> CompletionProvider:
    """A provider double; no dispatch happens at strategy-build time."""
    return MagicMock(spec=CompletionProvider)


def _app_state_with_retrieval(config: MemoryRetrievalConfig) -> AppState:
    """Build an app state whose memory backend is wired and retrieval tuned."""
    return make_app_state(
        config=RootConfig(
            company_name="test",
            memory=CompanyMemoryConfig(retrieval=config),
        ),
        memory_backend=MagicMock(spec=MemoryBackend),
    )


class TestMemoryInjectionStrategyAssembly:
    """A wired backend must yield a wired injection strategy."""

    def test_strategy_is_built_when_a_backend_is_wired(self) -> None:
        app_state = make_app_state(memory_backend=MagicMock(spec=MemoryBackend))

        strategy = build_memory_injection_strategy_or_none(
            app_state, provider=_provider(), cost_tracker=None
        )

        assert strategy is not None
        assert isinstance(strategy, MemoryInjectionStrategy)

    def test_no_strategy_without_a_backend(self) -> None:
        # Not a silent degrade: with no backend there is nothing to inject,
        # and the engine keeps its existing no-injection behaviour rather
        # than constructing a strategy over nothing.
        app_state = make_app_state()

        assert (
            build_memory_injection_strategy_or_none(
                app_state, provider=_provider(), cost_tracker=None
            )
            is None
        )

    def test_strategy_is_bound_to_the_wired_backend(self) -> None:
        backend = MagicMock(spec=MemoryBackend)
        app_state = make_app_state(memory_backend=backend)

        strategy = build_memory_injection_strategy_or_none(
            app_state, provider=_provider(), cost_tracker=None
        )

        assert app_state.slice(MemoryStateSlice).backend is backend
        assert strategy is not None


class TestRetrievalCollaboratorWiring:
    """Enabling an LLM stage builds its collaborator instead of crashing.

    Every collaborator flag defaults off, so the strategy constructors
    raising ``ValueError`` on a missing collaborator was a latent boot
    crash the first operator to enable a stage would hit. These guard
    that the assembly now supplies each collaborator the flag turns on.
    """

    def test_rerank_flag_builds_a_reranker(self) -> None:
        app_state = _app_state_with_retrieval(
            MemoryRetrievalConfig(
                strategy=InjectionStrategy.CONTEXT,
                query_specific_rerank_enabled=True,
            )
        )

        strategy = build_memory_injection_strategy_or_none(
            app_state, provider=_provider(), cost_tracker=None
        )

        assert isinstance(strategy, ContextInjectionStrategy)

    def test_hierarchical_retriever_flag_builds_the_retriever(self) -> None:
        app_state = _app_state_with_retrieval(
            MemoryRetrievalConfig(
                strategy=InjectionStrategy.CONTEXT,
                retriever="hierarchical",
            )
        )

        strategy = build_memory_injection_strategy_or_none(
            app_state, provider=_provider(), cost_tracker=None
        )

        assert isinstance(strategy, ContextInjectionStrategy)

    def test_reformulation_flag_builds_the_reformulation_pair(self) -> None:
        app_state = _app_state_with_retrieval(
            MemoryRetrievalConfig(
                strategy=InjectionStrategy.TOOL_BASED,
                query_reformulation_enabled=True,
            )
        )

        strategy = build_memory_injection_strategy_or_none(
            app_state, provider=_provider(), cost_tracker=None
        )

        assert isinstance(strategy, ToolBasedInjectionStrategy)


class TestWikiExporterAssembly:
    """The wiki exporter backing ``memory.browse_wiki`` tracks the backend."""

    def test_exporter_is_built_when_a_backend_is_wired(self) -> None:
        app_state = make_app_state(memory_backend=MagicMock(spec=MemoryBackend))

        exporter = wiki_exporter_or_none(app_state)

        assert isinstance(exporter, WikiExporter)

    def test_no_exporter_without_a_backend(self) -> None:
        assert wiki_exporter_or_none(make_app_state()) is None


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
