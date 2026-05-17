"""Tests for startup wiring helpers introduced by the log/startup audit.

Covers `_wire_workflow_observer`, `_wire_ontology_service`, the unconditional
tunnel wiring path, and the once-only contract on `set_ontology_service`.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import structlog

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.integrations_wiring import auto_wire_integrations
from synthorg.api.lifecycle import _wire_ontology_service
from synthorg.api.lifecycle_builder import _wire_workflow_observer
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)


def _make_state(**overrides: object) -> AppState:
    defaults: dict[str, object] = {
        "config": RootConfig(company_name="test"),
        "approval_store": ApprovalStore(),
    }
    defaults.update(overrides)
    return AppState(**defaults)  # type: ignore[arg-type]


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
        state._config_resolver = resolver  # type: ignore[assignment]
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

        assert state.has_ontology_service is True

    async def test_silently_returns_when_already_wired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _make_state()
        first = _FakeOntologyService("first")
        state.set_ontology_service(first)  # type: ignore[arg-type]

        async def fake_auto_wire(*args: Any, **kwargs: Any) -> Any:
            return _FakeOntologyService("second")

        monkeypatch.setattr(
            "synthorg.api.auto_wire.auto_wire_ontology",
            fake_auto_wire,
        )

        # Helper's try/except RuntimeError swallows the once-only setter rejection.
        await _wire_ontology_service(object(), state)  # type: ignore[arg-type]

        assert state.ontology_service is first  # type: ignore[comparison-overlap]


@pytest.mark.unit
class TestSetOntologyServiceOnceOnly:
    def test_second_call_raises_runtime_error(self) -> None:
        state = _make_state()
        state.set_ontology_service(_FakeOntologyService("first"))  # type: ignore[arg-type]
        with pytest.raises(RuntimeError):
            state.set_ontology_service(_FakeOntologyService("second"))  # type: ignore[arg-type]


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
