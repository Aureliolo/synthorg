"""Unit tests for ``_wire_meeting_conflict_bridge`` construction wiring.

The bridge is installed by post-construction injection, so a typo in the
orchestrator setter or a reordering that drops the conflict-resolution
service would silently disable the whole meeting-to-conflict pipeline with
no other test failing. These tests pin both the present-path (bridge wired
onto the orchestrator AND the slice) and the absent-paths (no-op).
"""

import pytest

from synthorg.api.construction_phase import _wire_meeting_conflict_bridge
from synthorg.api.state import AppState
from synthorg.communication.conflict_resolution.service import (
    ConflictResolutionService,
)
from synthorg.communication.meeting.conflict_escalation import (
    MeetingConflictEscalationBridge,
)
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.communication.state import CommunicationStateSlice
from synthorg.hr.registry import AgentRegistryService
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _orchestrator() -> MeetingOrchestrator:
    return MeetingOrchestrator(
        protocol_registry={},
        agent_caller=mock_of[AgentCaller](),
    )


def _app_state(*, with_service: bool) -> AppState:
    fields: dict[str, object] = {}
    if with_service:
        fields["conflict_resolution_service"] = mock_of[ConflictResolutionService]()
    return make_app_state(slices={CommunicationStateSlice: fields})


def test_bridge_wired_onto_orchestrator_and_slice() -> None:
    app_state = _app_state(with_service=True)
    orchestrator = _orchestrator()

    _wire_meeting_conflict_bridge(
        app_state,
        meeting_orchestrator=orchestrator,
        agent_registry=AgentRegistryService(),
        config_resolver=None,
    )

    slice_bridge = app_state.slice(CommunicationStateSlice).conflict_escalation_bridge
    assert isinstance(slice_bridge, MeetingConflictEscalationBridge)
    # The orchestrator holds the SAME instance the slice does, so the startup
    # resolver rebind (reaching the slice) updates the hook the meeting runs.
    assert orchestrator._conflict_escalation_hook is slice_bridge


def test_noop_when_orchestrator_absent() -> None:
    app_state = _app_state(with_service=True)

    _wire_meeting_conflict_bridge(
        app_state,
        meeting_orchestrator=None,
        agent_registry=AgentRegistryService(),
        config_resolver=None,
    )

    assert app_state.slice(CommunicationStateSlice).conflict_escalation_bridge is None


def test_noop_when_service_absent() -> None:
    app_state = _app_state(with_service=False)
    orchestrator = _orchestrator()

    _wire_meeting_conflict_bridge(
        app_state,
        meeting_orchestrator=orchestrator,
        agent_registry=AgentRegistryService(),
        config_resolver=None,
    )

    assert app_state.slice(CommunicationStateSlice).conflict_escalation_bridge is None
    assert orchestrator._conflict_escalation_hook is None


def test_wire_resolver_dependents_rebinds_bridge_resolver() -> None:
    # The bridge is built at construction with the placeholder resolver; the
    # startup rebind (_wire_resolver_dependents) must inject the live one so
    # the kill switch honours dashboard overrides.
    from synthorg.api.lifecycle_helpers.config_apply import (
        _wire_resolver_dependents,
    )
    from synthorg.settings.resolver import ConfigResolver
    from synthorg.settings.state import SettingsStateSlice

    resolver = mock_of[ConfigResolver]()
    bridge = MeetingConflictEscalationBridge(
        conflict_service=mock_of[ConflictResolutionService](),
        agent_registry=mock_of[AgentRegistryService](),
        config_resolver=None,
    )
    app_state = make_app_state(
        slices={
            SettingsStateSlice: {"config_resolver": resolver},
            CommunicationStateSlice: {"conflict_escalation_bridge": bridge},
        }
    )

    _wire_resolver_dependents(app_state)

    assert bridge._config_resolver is resolver
