"""Tests for startup wiring helpers.

Covers `_wire_workflow_observer`, `_wire_ontology_service`, the unconditional
tunnel wiring path, and the once-only contract on `set_ontology_service`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Never, cast, override
from unittest.mock import AsyncMock

import pytest
import structlog
from structlog.typing import EventDict
from typeguard import suppress_type_checks

from synthorg.api._tunnel_wiring import resolve_tunnel_state_dir, wire_tunnel_provider
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.channels import create_channels_plugin
from synthorg.api.integrations_wiring import auto_wire_integrations
from synthorg.api.lifecycle import _wire_ontology_service
from synthorg.api.lifecycle_builder import (
    _wire_approval_gate,
    _wire_workflow_observer,
)
from synthorg.api.lifecycle_helpers import narrative_wiring
from synthorg.api.lifecycle_helpers.conversational_wiring import (
    _guard_conversational_persistence,
)
from synthorg.api.lifecycle_helpers.finetune_wiring import (
    _wire_fine_tune_orchestrator,
)
from synthorg.api.lifecycle_helpers.startup_steps import _publish_red_team_runtime
from synthorg.api.lifecycle_runner_support import _wire_task_activity_observer
from synthorg.api.state import AppState
from synthorg.api.task_activity_observer import TaskActivityObserver
from synthorg.approval.state import ApprovalStateSlice
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.state import HrStateSlice
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.embedding.fine_tune_orchestrator import FineTuneOrchestrator
from synthorg.memory.embedding.training_sources import TrajectoryTrainingDataSource
from synthorg.memory.state import MemoryStateSlice
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.state import MetaStateSlice
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_WIRING_FAILED,
)
from synthorg.ontology.state import OntologyStateSlice
from synthorg.persistence.approval_protocol import ApprovalRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.redteam.builder import RedTeamRuntime
from synthorg.security.redteam.gate import RedTeamGateService
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of
from tests.unit.api.fakes_backend import FakePersistenceBackend


def _make_state(**overrides: object) -> AppState:
    defaults: dict[str, object] = {
        "config": RootConfig(company_name="test"),
        "approval_store": ApprovalStore(),
    }
    defaults.update(overrides)
    return make_app_state(**defaults)  # type: ignore[arg-type]  # **dict unpacking carries no 'slices' key


@dataclass
class _FakeTaskEngine:
    """Minimal TaskEngine stand-in covering `_wire_workflow_observer`'s surface."""

    _observers: list[object] = field(default_factory=list)
    registered: list[object] = field(default_factory=list)

    def register_observer(self, observer: object) -> None:
        self.registered.append(observer)
        self._observers.append(observer)

    def has_observer_type(self, observer_type: type[object]) -> bool:
        return any(isinstance(o, observer_type) for o in self._observers)


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

        # ``_FakeTaskEngine`` / ``_FakeWorkflowPersistence`` are structural
        # doubles for the concrete ``TaskEngine`` and the workflow repos; the
        # runtime check is suppressed for the same reason the static
        # ``# type: ignore[arg-type]`` is present (the test verifies wiring
        # behaviour, not type conformance of the fakes).
        with structlog.testing.capture_logs() as captured, suppress_type_checks():
            await _wire_workflow_observer(task_engine, persistence, state)  # type: ignore[arg-type]

        notes = [e for e in captured if e["event"] == API_APP_STARTUP]
        assert len(notes) == 1
        entry: EventDict = notes[0]
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

        # Structural doubles for the concrete ``TaskEngine`` / workflow repos;
        # suppress the runtime check at the same boundary as the static
        # ``# type: ignore[arg-type]`` (behavioural wiring test, not a type test).
        with structlog.testing.capture_logs() as captured, suppress_type_checks():
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
        # Observer's inner service captures the live resolver (depth is
        # resolved per activation, not cached at construction).
        inner = task_engine.registered[0]._service  # type: ignore[attr-defined]
        assert inner._config_resolver is resolver

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

        with suppress_type_checks():
            await _wire_workflow_observer(task_engine, persistence, state)  # type: ignore[arg-type]

        assert task_engine.registered == []

    async def test_skips_when_persistence_lacks_workflow_repos(self) -> None:
        state = _make_state()
        task_engine = _FakeTaskEngine()

        # `object()` has no workflow_definitions / workflow_executions attrs.
        with suppress_type_checks():
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

        async def fake_auto_wire(
            *args: object, **kwargs: object
        ) -> _FakeOntologyService:
            return service

        monkeypatch.setattr(
            "synthorg.api.auto_wire.auto_wire_ontology",
            fake_auto_wire,
        )

        with suppress_type_checks():
            await _wire_ontology_service(object(), state)  # type: ignore[arg-type]

        assert state.slice(OntologyStateSlice).service is not None

    async def test_silently_returns_when_already_wired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state()
        first = _FakeOntologyService("first")
        state.wire(OntologyStateSlice, service=first)

        async def fake_auto_wire(
            *args: object, **kwargs: object
        ) -> _FakeOntologyService:
            return _FakeOntologyService("second")

        monkeypatch.setattr(
            "synthorg.api.auto_wire.auto_wire_ontology",
            fake_auto_wire,
        )

        # The helper's slice-presence guard short-circuits when a service
        # is already wired, so the autowired "second" never replaces it.
        with suppress_type_checks():
            await _wire_ontology_service(object(), state)  # type: ignore[arg-type]

        assert cast(object, state.slice(OntologyStateSlice).service) is first


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

        with structlog.testing.capture_logs() as captured, suppress_type_checks():
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
        from synthorg.engine.park_service import ParkService

        existing = ApprovalGate(park_service=ParkService())
        state = _make_state()
        state.wire(ApprovalStateSlice, gate=existing)
        persistence = _FakeParkedPersistence(parked_contexts=_FakeParkedContextRepo())

        with suppress_type_checks():
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

    def test_state_dir_env_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYNTHORG_TUNNEL_STATE_DIR", "/data/tunnel")
        assert resolve_tunnel_state_dir() == Path("/data/tunnel")

    def test_state_dir_unset_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SYNTHORG_TUNNEL_STATE_DIR", raising=False)
        assert resolve_tunnel_state_dir() is None

    def test_state_dir_traversal_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYNTHORG_TUNNEL_STATE_DIR", "/data/../etc")
        with pytest.raises(ValueError, match="path traversal"):
            resolve_tunnel_state_dir()

    def test_hostile_state_dir_degrades_wiring_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: a traversal env value must not abort startup.

        ``wire_tunnel_provider`` is best-effort; the hostile value is
        rejected inside the try block and the tunnel card degrades to
        unavailable rather than the whole boot failing.
        """
        monkeypatch.setenv("SYNTHORG_TUNNEL_STATE_DIR", "/data/../etc")
        config = RootConfig(company_name="test")
        with structlog.testing.capture_logs() as captured:
            provider = wire_tunnel_provider(config)
        assert provider is None
        degrade_logs = [
            e
            for e in captured
            if e.get("service") == "tunnel_provider"
            and e.get("error_type") == "ValueError"
        ]
        assert len(degrade_logs) == 1


def _persistent_store() -> ApprovalStore:
    """Build an ApprovalStore whose ``has_persistent_repo`` is ``True``.

    Returns:
        A store wired to a spec'd durable repository double.
    """
    return ApprovalStore(repo=mock_of[ApprovalRepository]())


@pytest.mark.unit
class TestConversationalPersistenceGuard:
    """Propose/invite over a non-supporting persistent store fails fast.

    Both shipped backends now carry the conversational tables and admit
    the conversational ``approvals.source`` values, so the guard is a
    forward-looking capability check: any backend whose
    ``supports_conversational_approvals`` predicate is ``False`` must not
    run propose/invite over a persistent store, or the invite park's
    compensation would silently drop a parked approval mid-conversation.
    """

    def test_raises_when_invite_enabled_on_non_supporting_backend(self) -> None:
        with pytest.raises(ServiceUnavailableError):
            _guard_conversational_persistence(
                ChiefOfStaffConfig(invite_enabled=True),
                mock_of[PersistenceBackend](
                    backend_name="custom",
                    supports_conversational_approvals=False,
                ),
                _persistent_store(),
            )

    def test_raises_when_propose_enabled_on_non_supporting_backend(self) -> None:
        with pytest.raises(ServiceUnavailableError):
            _guard_conversational_persistence(
                ChiefOfStaffConfig(propose_enabled=True),
                mock_of[PersistenceBackend](
                    backend_name="custom",
                    supports_conversational_approvals=False,
                ),
                _persistent_store(),
            )

    def test_allows_in_memory_store_on_non_supporting_backend(self) -> None:
        # The default in-memory ApprovalStore never persists, so a
        # conversational source never reaches a backend table at all.
        _guard_conversational_persistence(
            ChiefOfStaffConfig(invite_enabled=True),
            mock_of[PersistenceBackend](
                backend_name="custom",
                supports_conversational_approvals=False,
            ),
            ApprovalStore(),
        )

    def test_allows_persistent_store_on_supporting_backend(self) -> None:
        # A backend that advertises conversational-approval durability may
        # run propose/invite over its durable store (both shipped backends).
        _guard_conversational_persistence(
            ChiefOfStaffConfig(invite_enabled=True),
            mock_of[PersistenceBackend](
                backend_name="sqlite",
                supports_conversational_approvals=True,
            ),
            _persistent_store(),
        )

    def test_allows_when_both_features_off(self) -> None:
        _guard_conversational_persistence(
            ChiefOfStaffConfig(),
            mock_of[PersistenceBackend](backend_name="sqlite"),
            _persistent_store(),
        )


@pytest.mark.unit
class TestFeatureWiringProposerDegradation:
    """A proposer wiring refusal degrades rather than reading as a fault."""

    async def test_proposer_guard_refusal_is_contained(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A propose/invite misconfiguration (propose enabled over a
        # persistent SQLite approval store) makes the guard raise. The
        # activation adapter must contain it, so the subsystem simply reads
        # as not-up and the rest of the pass is unaffected; a propagated
        # raise would mark it FAILED and, post-setup, refuse completion.
        from synthorg.api.subsystems import registry as subsystem_registry

        async def _refuse(*_args: object, **_kwargs: object) -> None:
            msg = "proposer boom"
            raise ServiceUnavailableError(msg)

        async def _si_config(
            *_args: object, **_kwargs: object
        ) -> SelfImprovementConfig:
            return SelfImprovementConfig()

        monkeypatch.setattr(
            "synthorg.api.lifecycle_helpers.conversational_wiring."
            "wire_chief_of_staff_proposer",
            _refuse,
        )
        monkeypatch.setattr(subsystem_registry, "_si_config", _si_config)

        app_state = _make_state()
        with suppress_type_checks():
            await subsystem_registry._activate_chief_of_staff_proposer(app_state)

        assert app_state.slice(MetaStateSlice).chief_of_staff_proposer is None


class _NoFineTuneBackend(FakePersistenceBackend):
    """Persistence backend whose fine-tune repo accessor is unsupported.

    Real backends without fine-tune support raise ``NotImplementedError``
    from the repo accessors (per the persistence protocol contract); this
    fake reproduces that so the wiring hook's skip path is exercised.
    """

    @property
    @override
    def fine_tune_runs(self) -> Never:
        msg = "backend does not support fine-tuning"
        raise NotImplementedError(msg)


def _wire_logs(captured: Sequence[EventDict]) -> list[EventDict]:
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

    async def test_unknown_query_provider_falls_back_to_extractive(self) -> None:
        fake = FakePersistenceBackend()
        fake.fine_tune_runs.mark_interrupted.return_value = 0

        async def _get_str(_namespace: object, key: str) -> str:
            return {
                "fine_tune_query_model": (
                    '{"provider": "ghost-provider", "model_id": "test-model"}'
                ),
            }.get(key, "")

        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(spec=ConfigResolver.get_str, side_effect=_get_str),
        )
        state = _make_state(
            config_resolver=resolver,
            provider_registry=ProviderRegistry(
                {"test-provider": mock_of[BaseCompletionProvider]()},
            ),
            slices={PersistenceStateSlice: {"backend": fake}},
        )

        with structlog.testing.capture_logs() as captured:
            await _wire_fine_tune_orchestrator(state)

        orchestrator = state.slice(MemoryStateSlice).fine_tune_orchestrator
        assert isinstance(orchestrator, FineTuneOrchestrator)
        # An operator-named provider that is not registered must NOT silently
        # substitute the first provider; the LLM query generator stays off and
        # the misconfiguration is surfaced (a clean degrade, so WARNING, matching
        # the sibling wiring helpers).
        assert orchestrator._query_generator is None
        warnings = [
            e
            for e in _wire_logs(captured)
            if e.get("log_level") == "warning"
            and "not registered" in str(e.get("note", ""))
        ]
        assert len(warnings) == 1
        assert warnings[0]["provider_name"] == "ghost-provider"

    async def test_wires_orchestrator_and_runs_recovery(self) -> None:
        fake = FakePersistenceBackend()
        fake.fine_tune_runs.mark_interrupted.return_value = 0
        # Empty query-model setting keeps the LLM query generator off, so the
        # orchestrator wires with the dependency-free extractive generator.
        resolver = mock_of[ConfigResolver](
            get_str=AsyncMock(spec=ConfigResolver.get_str, return_value=""),
        )
        state = _make_state(
            config_resolver=resolver,
            slices={PersistenceStateSlice: {"backend": fake}},
        )

        with structlog.testing.capture_logs() as captured:
            await _wire_fine_tune_orchestrator(state)

        orchestrator = state.slice(MemoryStateSlice).fine_tune_orchestrator
        assert isinstance(orchestrator, FineTuneOrchestrator)
        # No memory backend wired -> trajectory mode is unavailable, but the
        # orchestrator still wires so directory-mode runs work.
        assert orchestrator._training_data_source is None
        # No LLM query generator without an opt-in model id.
        assert orchestrator._query_generator is None
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
        # get_str is consulted for the trajectory scorecard-history dir AND
        # the (empty) fine-tune query-model setting; the empty model id keeps
        # the LLM query generator off.
        get_str_keys = {call.args[1] for call in resolver.get_str.await_args_list}
        assert get_str_keys == {"scorecard_history_dir", "fine_tune_query_model"}
        assert orchestrator._query_generator is None
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
        enabled = SelfImprovementConfig(
            chief_of_staff=ChiefOfStaffConfig(narrative_enabled=True)
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
                si_config=enabled,
            )

        failed = [
            e
            for e in captured
            if str(e.get("note", "")).startswith("narrator construction failed")
        ]
        assert len(failed) == 1
        assert failed[0]["error_type"] == "RuntimeError"

    async def test_reraises_interpreter_criticals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # MemoryError / RecursionError are interpreter-level criticals: the
        # best-effort handler must let them propagate, not swallow them.
        enabled = SelfImprovementConfig(
            chief_of_staff=ChiefOfStaffConfig(narrative_enabled=True)
        )

        def _boom(*_: object, **__: object) -> None:
            raise MemoryError

        monkeypatch.setattr(narrative_wiring, "_attach_narrator", _boom)
        state = _make_state(
            slices={
                PersistenceStateSlice: {"backend": FakePersistenceBackend()},
                EngineStateSlice: {"work_pipeline": mock_of[WorkPipeline]()},
            }
        )

        with pytest.raises(MemoryError):
            await narrative_wiring.wire_run_narrator(
                state,
                provider_registry=mock_of[ProviderRegistry](),
                cost_tracker=None,
                si_config=enabled,
            )


@pytest.mark.unit
class TestPublishRedTeamRuntime:
    """`_publish_red_team_runtime` publishes, clears, and gates the store."""

    def test_enabled_publishes_repo_and_attaches_gate(self) -> None:
        repo = InMemoryRedTeamReportRepository()
        gate = mock_of[RedTeamGateService]()
        runtime = mock_of[RedTeamRuntime](report_repo=repo, gate=gate)
        review_gate = mock_of[ReviewGateService]()
        state = _make_state()

        _publish_red_team_runtime(
            state, red_team_runtime=runtime, review_gate_service=review_gate
        )

        assert state.slice(SecurityStateSlice).red_team_reports is repo
        review_gate.set_red_team_gate.assert_called_once_with(gate)

    def test_disabled_clears_stale_repo(self) -> None:
        """An enabled -> disabled reinit must reset a previously-published store."""
        stale = InMemoryRedTeamReportRepository()
        state = _make_state(slices={SecurityStateSlice: {"red_team_reports": stale}})

        _publish_red_team_runtime(
            state,
            red_team_runtime=None,
            review_gate_service=mock_of[ReviewGateService](),
        )

        assert state.slice(SecurityStateSlice).red_team_reports is None

    def test_disabled_clears_gate(self) -> None:
        """An enabled -> disabled reinit must detach the previous gate, not leave it."""
        review_gate = mock_of[ReviewGateService]()
        state = _make_state()

        _publish_red_team_runtime(
            state, red_team_runtime=None, review_gate_service=review_gate
        )

        review_gate.set_red_team_gate.assert_called_once_with(None)

    def test_no_review_gate_is_a_noop_for_the_gate(self) -> None:
        """With no review gate wired, the store is still published, gate untouched."""
        repo = InMemoryRedTeamReportRepository()
        runtime = mock_of[RedTeamRuntime](
            report_repo=repo, gate=mock_of[RedTeamGateService]()
        )
        state = _make_state()

        _publish_red_team_runtime(
            state, red_team_runtime=runtime, review_gate_service=None
        )

        assert state.slice(SecurityStateSlice).red_team_reports is repo


@pytest.mark.unit
class TestWireTaskActivityObserver:
    """The boot hook that turns the dashboard live-activity feature on.

    Guards the reachability of the whole signal path: a flipped guard or a
    broken observer-registration check would silently ship the feature unwired.
    """

    def _state_with_tracker(self, *, tracker: PerformanceTracker | None) -> AppState:
        state = _make_state()
        if tracker is not None:
            state.wire(HrStateSlice, performance_tracker=tracker)
        return state

    def test_registers_observer_when_prerequisites_present(self) -> None:
        task_engine = _FakeTaskEngine()
        state = self._state_with_tracker(tracker=PerformanceTracker())
        persistence = FakePersistenceBackend()

        with structlog.testing.capture_logs() as captured, suppress_type_checks():
            _wire_task_activity_observer(
                task_engine,
                persistence,
                state,
                create_channels_plugin(),
            )

        assert len(task_engine.registered) == 1
        assert isinstance(task_engine.registered[0], TaskActivityObserver)
        wired = [
            e
            for e in captured
            if e["event"] == API_SERVICE_AUTO_WIRED
            and e.get("service") == "task_activity_observer"
        ]
        assert len(wired) == 1

    def test_skips_and_logs_when_channels_plugin_absent(self) -> None:
        task_engine = _FakeTaskEngine()
        state = self._state_with_tracker(tracker=PerformanceTracker())

        with structlog.testing.capture_logs() as captured, suppress_type_checks():
            # ``object()`` is not a ChannelsPlugin, so wiring must skip.
            _wire_task_activity_observer(
                task_engine,
                FakePersistenceBackend(),
                state,
                object(),
            )

        assert task_engine.registered == []
        skips = [
            e
            for e in captured
            if e["event"] == API_APP_STARTUP
            and e.get("component") == "task_activity_observer"
        ]
        assert len(skips) == 1

    def test_skips_and_logs_when_tracker_absent(self) -> None:
        task_engine = _FakeTaskEngine()
        state = self._state_with_tracker(tracker=None)

        with structlog.testing.capture_logs() as captured, suppress_type_checks():
            _wire_task_activity_observer(
                task_engine,
                FakePersistenceBackend(),
                state,
                create_channels_plugin(),
            )

        assert task_engine.registered == []
        skips = [
            e
            for e in captured
            if e["event"] == API_APP_STARTUP
            and e.get("component") == "task_activity_observer"
        ]
        assert len(skips) == 1

    def test_idempotent_when_observer_already_registered(self) -> None:
        existing = TaskActivityObserver.__new__(TaskActivityObserver)
        task_engine = _FakeTaskEngine(_observers=[existing])
        state = self._state_with_tracker(tracker=PerformanceTracker())

        with suppress_type_checks():
            _wire_task_activity_observer(
                task_engine,
                FakePersistenceBackend(),
                state,
                create_channels_plugin(),
            )

        assert task_engine.registered == []
