"""Unit tests for the conditional construction-phase facade wires.

``wire_construction`` always wires the dependency-free facades, but three
facades wire only when their construction-time primitive is present:
``artifact`` (artifact storage), ``integration_health`` (the health prober),
and ``simulation`` (the client simulation state). These tests assert both the
present-path (facade wired) and absent-path (facade stays ``None``).
"""

import pytest

from synthorg.api.auto_wire_meetings import MeetingWireResult
from synthorg.api.auto_wire_phase1 import Phase1Result
from synthorg.api.construction_wiring import ConstructionDeps
from synthorg.api.cursor import CursorSecret
from synthorg.api.integrations_wiring import IntegrationsBundle
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import ClientStateSlice
from synthorg.communication.event_stream.interrupt import InterruptStore
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.config.schema import RootConfig
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.infrastructure._construction import wire_construction
from synthorg.infrastructure.state import FacadesStateSlice
from synthorg.integrations.health.prober import HealthProberService
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.persistence.artifact_storage import ArtifactStorageBackend
from synthorg.security.audit import AuditLog
from synthorg.security.autonomy.protocol import AutonomyChangeStrategy
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _deps(
    *,
    performance_tracker: PerformanceTracker | None = None,
    audit_log: AuditLog | None = None,
    artifact_storage: ArtifactStorageBackend | None = None,
    prober: HealthProberService | None = None,
) -> ConstructionDeps:
    """Build a minimal ``ConstructionDeps`` for ``wire_construction``.

    Only the fields ``wire_construction`` reads are meaningful; the other
    required fields are typed doubles the wirer never touches.

    Returns:
        The assembled ``ConstructionDeps``.
    """
    return ConstructionDeps(
        effective_config=RootConfig(company_name="test"),
        phase1=mock_of[Phase1Result](),
        meeting_wire=mock_of[MeetingWireResult](),
        integrations=IntegrationsBundle(health_prober_service=prober),
        approval_store=mock_of[ApprovalStoreProtocol](),
        autonomy_change_strategy=mock_of[AutonomyChangeStrategy](),
        risk_tracker=RiskTracker(),
        notification_dispatcher=mock_of[NotificationDispatcher](),
        event_stream_hub=mock_of[EventStreamHub](),
        interrupt_store=mock_of[InterruptStore](),
        cursor_secret=mock_of[CursorSecret](),
        performance_tracker=performance_tracker,
        audit_log=audit_log,
        artifact_storage=artifact_storage,
    )


def _app_state(*, with_simulation: bool = False) -> AppState:
    """Compose an app state, optionally carrying a client simulation state.

    Returns:
        The composed ``AppState``.
    """
    fields: dict[str, object] = {}
    if with_simulation:
        fields["simulation_state"] = mock_of[ClientSimulationState]()
    return make_app_state(slices={ClientStateSlice: fields})


class TestArtifactFacadeWiring:
    async def test_wired_when_storage_present(self) -> None:
        app_state = _app_state()
        wire_construction(
            app_state, _deps(artifact_storage=mock_of[ArtifactStorageBackend]())
        )
        assert app_state.slice(FacadesStateSlice).artifact_facade_service is not None

    async def test_absent_without_storage(self) -> None:
        app_state = _app_state()
        wire_construction(app_state, _deps(artifact_storage=None))
        assert app_state.slice(FacadesStateSlice).artifact_facade_service is None


class TestIntegrationHealthFacadeWiring:
    async def test_wired_when_prober_present(self) -> None:
        app_state = _app_state()
        wire_construction(app_state, _deps(prober=mock_of[HealthProberService]()))
        assert (
            app_state.slice(FacadesStateSlice).integration_health_facade_service
            is not None
        )

    async def test_absent_without_prober(self) -> None:
        app_state = _app_state()
        wire_construction(app_state, _deps(prober=None))
        assert (
            app_state.slice(FacadesStateSlice).integration_health_facade_service is None
        )


class TestSimulationFacadeWiring:
    async def test_wired_when_simulation_state_present(self) -> None:
        app_state = _app_state(with_simulation=True)
        wire_construction(app_state, _deps())
        assert app_state.slice(FacadesStateSlice).simulation_facade_service is not None

    async def test_absent_without_simulation_state(self) -> None:
        app_state = _app_state(with_simulation=False)
        wire_construction(app_state, _deps())
        assert app_state.slice(FacadesStateSlice).simulation_facade_service is None
