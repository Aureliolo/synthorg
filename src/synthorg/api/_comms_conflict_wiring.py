"""Construction-phase wiring for the conflict-resolution service.

Extracted from :mod:`synthorg.api.construction_phase` to keep that
orchestrator under its size budget. Builds the boot-time company snapshot,
hands it to the conflict-resolution factory, and installs the resulting
service on the communication slice. Lives in the api layer because it
touches :class:`AppState`; the resolver-assembly knowledge stays in
:mod:`synthorg.communication.conflict_resolution.factory`.
"""

from typing import TYPE_CHECKING

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.config.schema import RootConfig

if TYPE_CHECKING:
    from synthorg.api.state import AppState


def wire_conflict_resolution_service(  # noqa: PLR0913 -- keyword-only collaborator DI
    app_state: AppState,
    *,
    effective_config: RootConfig,
    config: ConflictResolutionConfig,
    message_bus: MessageBus | None,
    escalation_store: EscalationQueueStore,
    escalation_processor: DecisionProcessor,
    escalation_registry: PendingFuturesRegistry,
) -> None:
    """Build + install the conflict-resolution service on the comms slice.

    The hierarchy is built from the boot-time company snapshot (same
    lifecycle as the escalation wiring), and the human resolver reuses the
    escalation store/processor/registry already wired.
    """
    from synthorg.communication.conflict_resolution.factory import (  # noqa: PLC0415
        build_conflict_resolution_service,
    )
    from synthorg.communication.state import CommunicationStateSlice  # noqa: PLC0415
    from synthorg.core.company import Company  # noqa: PLC0415

    company = Company(
        name=effective_config.company_name,
        type=effective_config.company_type,
        departments=effective_config.departments,
        config=effective_config.config,
        workflow_handoffs=effective_config.workflow_handoffs,
        escalation_paths=effective_config.escalation_paths,
    )
    service = build_conflict_resolution_service(
        config=config,
        company=company,
        escalation_store=escalation_store,
        escalation_processor=escalation_processor,
        escalation_registry=escalation_registry,
        event_hub=app_state.slice(CommunicationStateSlice).event_stream_hub,
        message_bus=message_bus,
    )
    app_state.wire(
        CommunicationStateSlice,
        conflict_resolution_service=service,
    )


__all__ = ["wire_conflict_resolution_service"]
