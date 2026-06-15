"""Best-effort gating of the _wire_signals_service startup helper."""

import pytest

from synthorg.api.lifecycle_helpers.feature_wiring import _wire_signals_service
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.state import MetaStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _approval_store() -> ApprovalStoreProtocol:
    return mock_of[ApprovalStoreProtocol]()


def _registry() -> AgentRegistryService:
    return mock_of[AgentRegistryService](active_agent_ids=lambda: ())


def _signals_of(app_state: object) -> object:
    return app_state.slice(MetaStateSlice).signals_service  # type: ignore[attr-defined]


class TestWireSignalsService:
    async def test_skips_without_persistence(self) -> None:
        app_state = make_app_state(performance_tracker=mock_of[PerformanceTracker]())
        await _wire_signals_service(
            app_state, effective_approval_store=_approval_store()
        )
        assert _signals_of(app_state) is None

    async def test_skips_without_tracker(self) -> None:
        app_state = make_app_state(persistence=mock_of[PersistenceBackend]())
        await _wire_signals_service(
            app_state, effective_approval_store=_approval_store()
        )
        assert _signals_of(app_state) is None

    async def test_wires_when_deps_present(self) -> None:
        app_state = make_app_state(
            persistence=mock_of[PersistenceBackend](),
            performance_tracker=mock_of[PerformanceTracker](),
            agent_registry=_registry(),
        )
        await _wire_signals_service(
            app_state, effective_approval_store=_approval_store()
        )
        assert isinstance(_signals_of(app_state), SignalsService)

    async def test_idempotent_when_already_wired(self) -> None:
        existing = mock_of[SignalsService]()
        app_state = make_app_state(
            persistence=mock_of[PersistenceBackend](),
            performance_tracker=mock_of[PerformanceTracker](),
            slices={MetaStateSlice: {"signals_service": existing}},
        )
        await _wire_signals_service(
            app_state, effective_approval_store=_approval_store()
        )
        assert _signals_of(app_state) is existing
