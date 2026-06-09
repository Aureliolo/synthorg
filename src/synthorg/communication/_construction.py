# module-kind: code
"""Communication feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.communication.state import CommunicationStateSlice

if TYPE_CHECKING:
    # api.* eagerly imports the communication slice this module wires; a
    # runtime import of api.construction_wiring / api.state forms a cycle.
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the base communication slice (bus, meetings, event stream).

    The escalation stack and the message / meeting services are wired in a
    later construction step that needs the settings config resolver.
    """
    meeting_wire = deps.meeting_wire
    app_state.swap_slice(
        CommunicationStateSlice.model_construct(
            message_bus=deps.phase1.message_bus,
            meeting_orchestrator=meeting_wire.meeting_orchestrator,
            meeting_scheduler=meeting_wire.meeting_scheduler,
            event_stream_hub=deps.event_stream_hub,
            interrupt_store=deps.interrupt_store,
            delegation_record_store=deps.delegation_record_store,
        )
    )
