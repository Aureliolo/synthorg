"""Characterization tests for construction-phase feature wiring.

Locks the construction-phase slice population that ``create_app`` performs
before the lifespan opens. As the historic central ``swap_slice`` block
migrates into per-feature ``construction_wirer`` hooks (run by
``run_construction_wiring``), these assertions guarantee every slice still
holds its construction-time service: a reorder that silently wires ``None``
fails here, where the build-only smoke tests would pass.
"""

import pytest

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.communication.state import CommunicationStateSlice
from synthorg.config.schema import RootConfig
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.security.state import SecurityStateSlice
from tests._shared import build_test_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.unit


@pytest.fixture(scope="class")
def built_app_state(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    cost_tracker: CostTracker,
    root_config: RootConfig,
) -> AppState:
    """Build the full app once and share it across the class's read-only asserts.

    Every test here only reads construction-wired slice fields, so the
    expensive ``create_app`` build is amortised class-wide rather than rebuilt
    per test (eight builds collapse to one). All four dependencies are
    session-scoped, so the identity assertions (``is fake_persistence`` etc.)
    still hold against the single shared build.
    """
    app = build_test_app(
        config=root_config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=cost_tracker,
    )
    state = app.state["app_state"]
    assert isinstance(state, AppState)
    return state


class TestConstructionWiringPopulatesSlices:
    """Every construction-wired slice field is non-``None`` after build."""

    def test_security_slice_wired(self, built_app_state: AppState) -> None:
        security = built_app_state.slice(SecurityStateSlice)
        assert security.audit_log is not None
        assert security.autonomy_change_strategy is not None

    def test_coordination_slice_wired(self, built_app_state: AppState) -> None:
        assert built_app_state.slice(CoordinationStateSlice).metrics_store is not None

    def test_approval_slice_wired(self, built_app_state: AppState) -> None:
        assert built_app_state.slice(ApprovalStateSlice).store is not None

    def test_notifications_slice_wired(self, built_app_state: AppState) -> None:
        assert built_app_state.slice(NotificationsStateSlice).dispatcher is not None

    def test_api_core_slice_wired(self, built_app_state: AppState) -> None:
        assert built_app_state.slice(ApiCoreStateSlice).cursor_secret is not None

    def test_communication_slice_wired(
        self,
        built_app_state: AppState,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        comms = built_app_state.slice(CommunicationStateSlice)
        assert comms.message_bus is fake_message_bus
        assert comms.event_stream_hub is not None
        assert comms.interrupt_store is not None

    def test_budget_slice_wired(
        self,
        built_app_state: AppState,
        cost_tracker: CostTracker,
    ) -> None:
        budget = built_app_state.slice(BudgetStateSlice)
        assert budget.cost_tracker is cost_tracker
        assert budget.cost_optimizer is not None

    def test_persistence_slice_wired(
        self,
        built_app_state: AppState,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        assert built_app_state.slice(PersistenceStateSlice).backend is fake_persistence
