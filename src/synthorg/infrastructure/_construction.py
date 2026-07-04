# module-kind: code
"""Facades feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.infrastructure.state import FacadesStateSlice

if TYPE_CHECKING:
    # Cycle breaker: ``api.construction_wiring`` pulls a cold-import cycle, so
    # ``ConstructionDeps`` is named for signatures only.
    from synthorg.api.construction_wiring import ConstructionDeps


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Wire the read / MCP facades onto the facades slice.

    The dependency-free facades (review, setup, project, requests,
    template-pack, client) and the OAuth registry always wire. The
    construction-dependent facades wire only when their backing primitive is
    present: quality projects the performance tracker; events/audit/artifact/
    integration-health read their construction-time primitives; simulation
    reads the client feature's simulation state (the source of the
    ``depends_on=("client",)`` edge). ``OAuthFacadeService`` accepts a ``None``
    token manager, so it wires unconditionally. Partial ``wire`` preserves the
    facade fields that sibling wirers populate at the persistence and
    settings phases (user, backup, ontology, mcp-catalog, provider-read).
    """
    from synthorg.client.state import ClientStateSlice  # noqa: PLC0415
    from synthorg.engine.quality.mcp_services import (  # noqa: PLC0415
        QualityFacadeService,
        ReviewFacadeService,
    )
    from synthorg.infrastructure.services import (  # noqa: PLC0415
        AuditReadService,
        EventsReadService,
        IntegrationHealthFacadeService,
        ProjectFacadeService,
        RequestsFacadeService,
        SetupFacadeService,
        SimulationFacadeService,
        TemplatePackFacadeService,
    )
    from synthorg.integrations.mcp_facades import (  # noqa: PLC0415
        ArtifactFacadeService,
        ClientFacadeService,
        OAuthFacadeService,
    )

    app_state.wire(
        FacadesStateSlice,
        review_facade_service=ReviewFacadeService(),
        setup_facade_service=SetupFacadeService(),
        project_facade_service=ProjectFacadeService(),
        requests_facade_service=RequestsFacadeService(),
        template_pack_facade_service=TemplatePackFacadeService(),
        client_facade_service=ClientFacadeService(),
        events_read_service=EventsReadService(hub=deps.event_stream_hub),
        oauth_facade_service=OAuthFacadeService(
            token_manager=deps.integrations.oauth_token_manager,
        ),
    )
    if deps.performance_tracker is not None:
        app_state.wire(
            FacadesStateSlice,
            quality_facade_service=QualityFacadeService(
                tracker=deps.performance_tracker,
            ),
        )
    if deps.audit_log is not None:
        app_state.wire(
            FacadesStateSlice,
            audit_read_service=AuditReadService(audit_log=deps.audit_log),
        )
    if deps.artifact_storage is not None:
        app_state.wire(
            FacadesStateSlice,
            artifact_facade_service=ArtifactFacadeService(
                storage=deps.artifact_storage,
            ),
        )
    prober = deps.integrations.health_prober_service
    if prober is not None:
        app_state.wire(
            FacadesStateSlice,
            integration_health_facade_service=IntegrationHealthFacadeService(
                prober=prober,
            ),
        )
    simulation_state = app_state.slice(ClientStateSlice).simulation_state
    if simulation_state is not None:
        app_state.wire(
            FacadesStateSlice,
            simulation_facade_service=SimulationFacadeService(
                state=simulation_state,
            ),
        )
