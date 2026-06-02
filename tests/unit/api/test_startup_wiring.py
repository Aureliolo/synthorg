"""Tests for startup wiring helpers introduced by the log/startup audit.

Covers `_wire_workflow_observer`, `_wire_ontology_service`, the unconditional
tunnel wiring path, and the once-only contract on `set_ontology_service`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast, override
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.integrations_wiring import auto_wire_integrations
from synthorg.api.lifecycle import _wire_ontology_service
from synthorg.api.lifecycle_builder import (
    _wire_approval_gate,
    _wire_workflow_observer,
)
from synthorg.api.lifecycle_helpers import narrative_wiring
from synthorg.api.lifecycle_helpers.feature_wiring import (
    _guard_conversational_persistence,
)
from synthorg.api.lifecycle_helpers.finetune_wiring import (
    _wire_fine_tune_orchestrator,
)
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.state import EngineStateSlice
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.embedding.fine_tune_orchestrator import FineTuneOrchestrator
from synthorg.memory.embedding.training_sources import TrajectoryTrainingDataSource
from synthorg.memory.state import MemoryStateSlice
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.config import load_self_improvement_config
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_WIRING_FAILED,
)
from synthorg.ontology.state import OntologyStateSlice
from synthorg.persistence.approval_protocol import ApprovalRepository
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes_backend import FakePersistenceBackend


def _make_state(**overrides: object) -> AppState:
    defaults: dict[str, Any] = {
        "config": RootConfig(company_name="test"),
        "approval_store": ApprovalStore(),
    }
    defaults.update(overrides)
    return make_app_state(**defaults)


@dataclass
class _FakeTaskEngine:
    """Minimal TaskEngine stand-in covering `_wire_workflow_observer`'s surface."""

    _observers: list[object] = field(default_factory=list)
    registered: list[object] = field(default_factory=list)

    def register_observer(self, observer: object) -> None:
        self.registered.append(observer)
        self._observers.append(observer)


@dataclass
class _FakeWorkflowPersistence:
    """Minimal PersistenceBackend stand-in carrying the workflow repo attrs."""

    workflow_definitions: object
    workflow_executions: object


@dataclass
class _FakeEngineBridge:
    max_subworkflow_depth: int


class _FakeConfigResolver:
    def __init__(self, max_depth: int) -> None:
        self._max_depth = max_depth

    async def get_engine_bridge_config(self) -> _FakeEngineBridge:
        return _FakeEngineBridge(max_subworkflow_depth=self._max_depth)


@pytest.mark.unit
class TestWireWorkflowObserver:
    async def test_registers_observer_with_seed_default_when_resolver_absent(
        self,
    ) -> None:
        state = _make_state()
        persistence = _FakeWorkflowPersistence(
            workflow_definitions=object(),
            workflow_executions=object(),
        )
        task_engine = _FakeTaskEngine()

        with structlog.testing.capture_logs() as captured:
            await _wire_workflow_observer(task_engine, persistence, state)  # type: ignore[arg-type]

        notes = [e for e in captured if e["event"] == API_APP_STARTUP]
        assert len(notes) == 1
        entry: Any = notes[0]
        assert entry["log_level"] == "info"
        assert entry["component"] == "workflow_execution_observer"
        assert "config_resolver not wired" in entry["note"]
        assert len(task_engine.registered) == 1

    async def test_uses_resolver_max_depth_when_resolver_wired(self) -> None:
        state = _make_state()
        resolver = _FakeConfigResolver(max_depth=7)
        state.wire(SettingsStateSlice, config_resolver=resolver)
        persistence = _FakeWorkflowPersistence(
            workflow_definitions=object(),
            workflow_executions=object(),
        )
        task_engine = _FakeTaskEngine()

        with structlog.testing.capture_logs() as captured:
            await _wire_workflow_observer(task_engine, persistence, state)  # type: ignore[arg-type]

        # No fallback INFO log: the resolver was used.
        fallback_logs = [
            e
            for e in captured
            if e["event"] == API_APP_STARTUP
            and e.get("component") == "workflow_execution_observer"
        ]
        assert fallback_logs == []
        assert len(task_engine.registered) == 1
        # Observer's inner service captures the depth.
        inner = task_engine.registered[0]._service  # type: ignore[attr-defined]
        assert inner._max_subworkflow_depth == 7

    async def test_idempotent_when_observer_already_registered(self) -> None:
        from synthorg.engine.workflow.execution_observer import (
            WorkflowExecutionObserver,
        )

        state = _make_state()
        persistence = _FakeWorkflowPersistence(
            workflow_definitions=object(),
            workflow_executions=object(),
        )
        existing = WorkflowExecutionObserver.__new__(WorkflowExecutionObserver)
        task_engine = _FakeTaskEngine(_observers=[existing])

        await _wire_workflow_observer(task_engine, persistence, state)  # type: ignore[arg-type]

        assert task_engine.registered == []

    async def test_skips_when_persistence_lacks_workflow_repos(self) -> None:
        state = _make_state()
        task_engine = _FakeTaskEngine()

        # `object()` has no workflow_definitions / workflow_executions attrs.
        await _wire_workflow_observer(task_engine, object(), state)  # type: ignore[arg-type]

        assert task_engine.registered == []


class _FakeOntologyService:
    def __init__(self, name: str = "fake") -> None:
        self.name = name


@pytest.mark.unit
class TestWireOntologyService:
    async def test_wires_service_when_auto_wire_returns_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state()
        service = _FakeOntologyService("first")

        async def fake_auto_wire(*args: Any, **kwargs: Any) -> Any:
            return service

        monkeypatch.setattr(
            "synthorg.api.auto_wire.auto_wire_ontology",
            fake_auto_wire,
        )

        await _wire_ontology_service(object(), state)  # type: ignore[arg-type]

        assert state.slice(OntologyStateSlice).service is not None

    async def test_silently_returns_when_already_wired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state()
        first = _FakeOntologyService("first")
        state.wire(OntologyStateSlice, service=first)

        async def fake_auto_wire(*args: Any, **kwargs: Any) -> Any:
            return _FakeOntologyService("second")

        monkeypatch.setattr(
            "synthorg.api.auto_wire.auto_wire_ontology",
            fake_auto_wire,
        )

        # The helper's slice-presence guard short-circuits when a service
        # is already wired, so the autowired "second" never replaces it.
        await _wire_ontology_service(object(), state)  # type: ignore[arg-type]

        assert cast(Any, state.slice(OntologyStateSlice).service) is first


@dataclass
class _FakeParkedContextRepo:
    """Stand-in for the persistence ParkedContextRepository."""

    saved: list[object] = field(default_factory=list)


@dataclass
class _FakeParkedPersistence:
    """Minimal connected PersistenceBackend exposing parked_contexts."""

    parked_contexts: _FakeParkedContextRepo
    is_connected: bool = True


@pytest.mark.unit
class TestWireApprovalGate:
    """The single boot ApprovalGate is wired once persistence connects."""

    async def test_wires_gate_with_persistence_parked_repo(self) -> None:
        state = _make_state()
        repo = _FakeParkedContextRepo()
        persistence = _FakeParkedPersistence(parked_contexts=repo)

        with structlog.testing.capture_logs() as captured:
            await _wire_approval_gate(persistence, state)  # type: ignore[arg-type]

        gate = state.slice(ApprovalStateSlice).gate
        assert gate is not None
        # ``id`` rather than ``is``: the fake is not typed as the
        # protocol, so a direct identity check trips mypy's
        # non-overlapping-identity guard while asserting the same fact.
        assert id(gate._parked_context_repo) == id(repo)
        wired = [
            e
            for e in captured
            if e["event"] == API_SERVICE_AUTO_WIRED
            and e.get("service") == "approval_gate"
        ]
        assert len(wired) == 1

    async def test_idempotent_when_gate_already_wired(self) -> None:
        from synthorg.engine.approval_gate import ApprovalGate
        from synthorg.security.timeout.park_service import ParkService

        existing = ApprovalGate(park_service=ParkService())
        state = _make_state()
        state.wire(ApprovalStateSlice, gate=existing)
        persistence = _FakeParkedPersistence(parked_contexts=_FakeParkedContextRepo())

        await _wire_approval_gate(persistence, state)  # type: ignore[arg-type]

        assert state.slice(ApprovalStateSlice).gate is existing

    async def test_gate_built_without_repo_when_persistence_absent(self) -> None:
        state = _make_state()

        await _wire_approval_gate(None, state)

        gate = state.slice(ApprovalStateSlice).gate
        assert gate is not None
        assert gate._parked_context_repo is None


@pytest.mark.unit
class TestTunnelUnconditionalWiring:
    def test_tunnel_provider_wired_when_integrations_disabled(
        self, tmp_path: Path
    ) -> None:
        config = RootConfig(company_name="test")
        # integrations.enabled defaults to False on the stock RootConfig.

        with structlog.testing.capture_logs() as captured:
            bundle = auto_wire_integrations(
                effective_config=config,
                persistence=None,
                message_bus=None,
                api_config=config.api,
                ceremony_scheduler=None,
                db_url="sqlite:///:memory:",
                resolved_db_path=tmp_path / "synthorg.db",
                boot_db_path="",
            )

        assert bundle.tunnel_provider is not None
        tunnel_logs = [
            e
            for e in captured
            if e["event"] == API_SERVICE_AUTO_WIRED
            and e.get("service") == "tunnel_provider"
        ]
        assert len(tunnel_logs) == 1


@dataclass
class _FakeBackend:
    """Minimal PersistenceBackend stand-in carrying only ``backend_name``."""

    backend_name: str


def _persistent_store() -> ApprovalStore:
    """Build an ApprovalStore whose ``has_persistent_repo`` is ``True``.

    Returns:
        A store wired to a spec'd durable repository double.
    """
    return ApprovalStore(repo=mock_of[ApprovalRepository]())


@pytest.mark.unit
class TestConversationalPersistenceGuard:
    """The propose/invite + persistent-SQLite combo fails fast at startup.

    The SQLite ``approvals.source`` CHECK omits the conversational
    sources, so a propose- or invite-produced approval cannot durably
    persist there. The guard raises rather than letting the invite park's
    compensation silently drop a parked approval mid-conversation.
    """

    def test_raises_when_invite_enabled_on_persistent_sqlite(self) -> None:
        with pytest.raises(ServiceUnavailableError):
            _guard_conversational_persistence(
                ChiefOfStaffConfig(invite_enabled=True),
                _FakeBackend(backend_name="sqlite"),  # type: ignore[arg-type]
                _persistent_store(),
            )

    def test_raises_when_propose_enabled_on_persistent_sqlite(self) -> None:
        with pytest.raises(ServiceUnavailableError):
            _guard_conversational_persistence(
                ChiefOfStaffConfig(propose_enabled=True),
                _FakeBackend(backend_name="sqlite"),  # type: ignore[arg-type]
                _persistent_store(),
            )

    def test_allows_in_memory_store_on_sqlite(self) -> None:
        # The default in-memory ApprovalStore never persists, so a
        # conversational source never reaches the SQLite table.
        _guard_conversational_persistence(
            ChiefOfStaffConfig(invite_enabled=True),
            _FakeBackend(backend_name="sqlite"),  # type: ignore[arg-type]
            ApprovalStore(),
        )

    def test_allows_persistent_store_on_postgres(self) -> None:
        # Postgres widened its source CHECK, so the durable store is fine.
        _guard_conversational_persistence(
            ChiefOfStaffConfig(invite_enabled=True),
            _FakeBackend(backend_name="postgres"),  # type: ignore[arg-type]
            _persistent_store(),
        )

    def test_allows_when_both_features_off(self) -> None:
        _guard_conversational_persistence(
            ChiefOfStaffConfig(),
            _FakeBackend(backend_name="sqlite"),  # type: ignore[arg-type]
            _persistent_store(),
        )


class _NoFineTuneBackend(FakePersistenceBackend):
    """Persistence backend whose fine-tune repo accessor is unsupported.

    Real backends without fine-tune support raise ``NotImplementedError``
    from the repo accessors (per the persistence protocol contract); this
    fake reproduces that so the wiring hook's skip path is exercised.
    """

    @override
    @property
    def fine_tune_runs(self) -> Any:
        msg = "backend does not support fine-tuning"
        raise NotImplementedError(msg)


def _wire_logs(captured: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Filter captured logs to the fine-tune-orchestrator startup events.

    Returns:
        The startup-event records emitted by the wiring hook.
    """
    return [
        e
        for e in captured
        if e["event"] == API_APP_STARTUP
        and e.get("service") == "fine_tune_orchestrator"
    ]


@pytest.mark.unit
class TestWireFineTuneOrchestrator:
    """The embedding fine-tune orchestrator is wired once persistence connects."""

    async def test_skips_when_persistence_absent(self) -> None:
        state = _make_state()

        await _wire_fine_tune_orchestrator(state)

        assert state.slice(MemoryStateSlice).fine_tune_orchestrator is None

    async def test_wires_orchestrator_and_runs_recovery(self) -> None:
        fake = FakePersistenceBackend()
        fake.fine_tune_runs.mark_interrupted.return_value = 0
        state = _make_state(slices={PersistenceStateSlice: {"backend": fake}})

        with structlog.testing.capture_logs() as captured:
            await _wire_fine_tune_orchestrator(state)

        orchestrator = state.slice(MemoryStateSlice).fine_tune_orchestrator
        assert isinstance(orchestrator, FineTuneOrchestrator)
        # No memory backend wired -> trajectory mode is unavailable, but the
        # orchestrator still wires so directory-mode runs work.
        assert orchestrator._training_data_source is None
        fake.fine_tune_runs.mark_interrupted.assert_awaited_once()
        wired = [e for e in _wire_logs(captured) if e.get("note") == "wired"]
        assert len(wired) == 1
        assert wired[0]["trajectory_source"] is False

    async def test_attaches_trajectory_source_when_memory_backend_present(
        self,
    ) -> None:
        fake = FakePersistenceBackend()
        fake.fine_tune_runs.mark_interrupted.return_value = 0
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(spec=ConfigResolver.get_str, return_value=""),
        )
        state = _make_state(
            memory_backend=InMemoryBackend(),
            config_resolver=resolver,
            slices={PersistenceStateSlice: {"backend": fake}},
        )

        with structlog.testing.capture_logs() as captured:
            await _wire_fine_tune_orchestrator(state)

        orchestrator = state.slice(MemoryStateSlice).fine_tune_orchestrator
        assert isinstance(orchestrator, FineTuneOrchestrator)
        assert isinstance(
            orchestrator._training_data_source, TrajectoryTrainingDataSource
        )
        resolver.get_str.assert_awaited_once()
        wired = [e for e in _wire_logs(captured) if e.get("note") == "wired"]
        assert wired[0]["trajectory_source"] is True

    async def test_idempotent_when_already_wired(self) -> None:
        existing = mock_of[FineTuneOrchestrator]()
        fake = FakePersistenceBackend()
        state = _make_state(
            fine_tune_orchestrator=existing,
            slices={PersistenceStateSlice: {"backend": fake}},
        )

        await _wire_fine_tune_orchestrator(state)

        assert state.slice(MemoryStateSlice).fine_tune_orchestrator is existing
        fake.fine_tune_runs.mark_interrupted.assert_not_awaited()

    async def test_skips_when_backend_lacks_fine_tune_support(self) -> None:
        state = _make_state(
            slices={PersistenceStateSlice: {"backend": _NoFineTuneBackend()}}
        )

        with structlog.testing.capture_logs() as captured:
            await _wire_fine_tune_orchestrator(state)

        assert state.slice(MemoryStateSlice).fine_tune_orchestrator is None
        skipped = [
            e
            for e in _wire_logs(captured)
            if "lacks fine-tune support" in e.get("note", "")
        ]
        assert len(skipped) == 1

    async def test_degrades_when_recovery_raises(self) -> None:
        fake = FakePersistenceBackend()
        fake.fine_tune_runs.mark_interrupted.side_effect = RuntimeError("db down")
        state = _make_state(slices={PersistenceStateSlice: {"backend": fake}})

        with structlog.testing.capture_logs() as captured:
            await _wire_fine_tune_orchestrator(state)

        # A wiring failure leaves the controllers to 501 rather than
        # poisoning startup.
        assert state.slice(MemoryStateSlice).fine_tune_orchestrator is None
        degraded = [
            e
            for e in captured
            if e["event"] == MEMORY_FINE_TUNE_WIRING_FAILED
            and e.get("operation") == "startup_wire"
        ]
        assert len(degraded) == 1


@pytest.mark.unit
class TestWireRunNarrator:
    """The post-run narrator wires best-effort behind narrative_enabled."""

    async def test_best_effort_when_attach_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A narrator-construction failure must not abort startup: the
        # pipeline is simply left narrator-less and the failure is logged.
        enabled = SimpleNamespace(
            chief_of_staff=ChiefOfStaffConfig(narrative_enabled=True)
        )
        monkeypatch.setattr(
            "synthorg.meta.config.load_self_improvement_config",
            AsyncMock(spec=load_self_improvement_config, return_value=enabled),
        )

        def _boom(*_: object, **__: object) -> None:
            msg = "provider exploded"
            raise RuntimeError(msg)

        monkeypatch.setattr(narrative_wiring, "_attach_narrator", _boom)
        state = _make_state(
            slices={
                PersistenceStateSlice: {"backend": FakePersistenceBackend()},
                EngineStateSlice: {"work_pipeline": mock_of[WorkPipeline]()},
            }
        )

        with structlog.testing.capture_logs() as captured:
            await narrative_wiring.wire_run_narrator(
                state,
                provider_registry=mock_of[ProviderRegistry](),
                cost_tracker=None,
            )

        failed = [
            e
            for e in captured
            if str(e.get("note", "")).startswith("narrator construction failed")
        ]
        assert len(failed) == 1
        assert failed[0]["error_type"] == "RuntimeError"
