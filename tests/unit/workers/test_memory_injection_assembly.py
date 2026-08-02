"""Tests for the memory collaborators threaded into the boot AgentEngine.

The regression these guard is the defining one: ``_construct_agent_engine``
passed ``memory_backend`` but never ``memory_injection_strategy``, so
``AgentEngine._retrieve_injected_memory_messages`` short-circuited on
every task and no agent ever received a memory it had not explicitly
asked for.
"""

from unittest.mock import MagicMock

import pytest

from synthorg.api.lifecycle_assembly import assemble_lifespan_hooks
from synthorg.api.state import AppState
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.config.schema import RootConfig
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.consolidation.wiki_export import WikiExporter
from synthorg.memory.injection import InjectionStrategy, MemoryInjectionStrategy
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.state import MemoryStateSlice
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from synthorg.notifications.dispatcher import NotificationDispatcher
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
    """Both memory backends must exist before runtime services read them.

    Every consumer of the org layer asks for it while assembling an agent,
    and the engine reads the memory slice eagerly at construction. A
    backend wired after runtime services install is read as ``None`` there
    and stays unreachable for the life of the process, however healthy it
    looks afterwards.
    """

    def test_reconcile_precedes_runtime_services(self) -> None:
        # Asserted against the assembled hook list, not the module's source
        # text: the order that matters is the one the app actually runs, and
        # a source scan agrees with it only until someone reformats the file.
        startup, _ = assemble_lifespan_hooks(
            make_app_state(),
            persistence=None,
            message_bus=None,
            bridge=None,
            settings_dispatcher=None,
            task_engine=None,
            meeting_scheduler=None,
            backup_service=None,
            approval_timeout_scheduler=None,
            should_auto_wire_settings=False,
            effective_config=RootConfig(company_name="test"),
            connection_catalog=None,
            provider_registry=None,
            cost_tracker=None,
            performance_tracker=None,
            notification_dispatcher=MagicMock(spec=NotificationDispatcher),
        )
        names = [getattr(hook, "__name__", "") for hook in startup]

        assert "_reconcile_subsystems" in names
        assert "_install_runtime_services" in names
        assert names.index("_reconcile_subsystems") < names.index(
            "_install_runtime_services"
        )

    def test_both_memory_backends_are_declared(self) -> None:
        # Declared, so the pass that runs before runtime services brings
        # them up. Asserting the declaration rather than a call inside a
        # named hook keeps this tied to what actually decides the order.
        declared = {spec.name for spec in SUBSYSTEMS}

        assert {"memory_backend", "org_memory_backend"} <= declared
