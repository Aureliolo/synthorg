"""Facades feature state slice.

Holds the read / MCP facade services that aggregate domain services
for the dashboard and MCP read tools: audit, events, backup, client,
artifact, OAuth, ontology, MCP-catalog, integration-health, project,
project-doc-memory, provider-read, quality, requests, review, setup,
simulation, template-pack, and user facades. All are wired lazily once
their backing services exist and are ``None`` until then; readers guard
accordingly.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.docs_engine.retrieval_facade import (
    ProjectAwareMemoryFacade,
)
from synthorg.engine.quality.mcp_services import (
    QualityFacadeService,
    ReviewFacadeService,
)
from synthorg.infrastructure.services import (
    AuditReadService,
    BackupFacadeService,
    EventsReadService,
    IntegrationHealthFacadeService,
    ProjectFacadeService,
    ProviderReadService,
    RequestsFacadeService,
    SetupFacadeService,
    SimulationFacadeService,
    TemplatePackFacadeService,
    UserFacadeService,
)
from synthorg.integrations.mcp_services import (
    ArtifactFacadeService,
    ClientFacadeService,
    MCPCatalogFacadeService,
    OAuthFacadeService,
    OntologyFacadeService,
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class FacadesStateSlice(BaseFeatureStateSlice):
    """Application-state slice for the read / MCP facade services."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_facade_service: ArtifactFacadeService | None = None
    audit_read_service: AuditReadService | None = None
    backup_facade_service: BackupFacadeService | None = None
    client_facade_service: ClientFacadeService | None = None
    events_read_service: EventsReadService | None = None
    integration_health_facade_service: IntegrationHealthFacadeService | None = None
    mcp_catalog_facade_service: MCPCatalogFacadeService | None = None
    oauth_facade_service: OAuthFacadeService | None = None
    ontology_facade_service: OntologyFacadeService | None = None
    project_facade_service: ProjectFacadeService | None = None
    project_doc_memory_facade: ProjectAwareMemoryFacade | None = None
    provider_read_service: ProviderReadService | None = None
    quality_facade_service: QualityFacadeService | None = None
    requests_facade_service: RequestsFacadeService | None = None
    review_facade_service: ReviewFacadeService | None = None
    setup_facade_service: SetupFacadeService | None = None
    simulation_facade_service: SimulationFacadeService | None = None
    template_pack_facade_service: TemplatePackFacadeService | None = None
    user_facade_service: UserFacadeService | None = None


def _facade[FacadeT](value: FacadeT | None, label: str) -> FacadeT:
    """Return *value* or raise 503 (thin alias of ``require_service``).

    Returns:
        The non-``None`` facade service.
    """
    return require_service(value, label)


def audit_read_service_of(app_state: AppStateSliceMixin) -> AuditReadService:
    """Resolve the audit read service from its slice, or raise 503.

    Returns:
        The wired audit read service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).audit_read_service, "Audit Read Service"
    )


def backup_facade_service_of(app_state: AppStateSliceMixin) -> BackupFacadeService:
    """Resolve the backup facade service from its slice, or raise 503.

    Returns:
        The wired backup facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).backup_facade_service,
        "Backup Facade Service",
    )


def events_read_service_of(app_state: AppStateSliceMixin) -> EventsReadService:
    """Resolve the events read service from its slice, or raise 503.

    Returns:
        The wired events read service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).events_read_service, "Events Read Service"
    )


def integration_health_facade_service_of(
    app_state: AppStateSliceMixin,
) -> IntegrationHealthFacadeService:
    """Resolve the integration-health facade service, or raise 503.

    Returns:
        The wired integration-health facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).integration_health_facade_service,
        "Integration Health Facade Service",
    )


def project_facade_service_of(app_state: AppStateSliceMixin) -> ProjectFacadeService:
    """Resolve the project facade service from its slice, or raise 503.

    Returns:
        The wired project facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).project_facade_service,
        "Project Facade Service",
    )


def provider_read_service_of(app_state: AppStateSliceMixin) -> ProviderReadService:
    """Resolve the provider read service from its slice, or raise 503.

    Returns:
        The wired provider read service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).provider_read_service,
        "Provider Read Service",
    )


def requests_facade_service_of(
    app_state: AppStateSliceMixin,
) -> RequestsFacadeService:
    """Resolve the requests facade service from its slice, or raise 503.

    Returns:
        The wired requests facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).requests_facade_service,
        "Requests Facade Service",
    )


def setup_facade_service_of(app_state: AppStateSliceMixin) -> SetupFacadeService:
    """Resolve the setup facade service from its slice, or raise 503.

    Returns:
        The wired setup facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).setup_facade_service,
        "Setup Facade Service",
    )


def simulation_facade_service_of(
    app_state: AppStateSliceMixin,
) -> SimulationFacadeService:
    """Resolve the simulation facade service from its slice, or raise 503.

    Returns:
        The wired simulation facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).simulation_facade_service,
        "Simulation Facade Service",
    )


def template_pack_facade_service_of(
    app_state: AppStateSliceMixin,
) -> TemplatePackFacadeService:
    """Resolve the template-pack facade service from its slice, or raise 503.

    Returns:
        The wired template-pack facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).template_pack_facade_service,
        "Template Pack Facade Service",
    )


def user_facade_service_of(app_state: AppStateSliceMixin) -> UserFacadeService:
    """Resolve the user facade service from its slice, or raise 503.

    Returns:
        The wired user facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).user_facade_service,
        "User Facade Service",
    )


def artifact_facade_service_of(
    app_state: AppStateSliceMixin,
) -> ArtifactFacadeService:
    """Resolve the artifact facade service from its slice, or raise 503.

    Returns:
        The wired artifact facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).artifact_facade_service,
        "Artifact Facade Service",
    )


def client_facade_service_of(app_state: AppStateSliceMixin) -> ClientFacadeService:
    """Resolve the client facade service from its slice, or raise 503.

    Returns:
        The wired client facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).client_facade_service,
        "Client Facade Service",
    )


def mcp_catalog_facade_service_of(
    app_state: AppStateSliceMixin,
) -> MCPCatalogFacadeService:
    """Resolve the MCP-catalog facade service from its slice, or raise 503.

    Returns:
        The wired MCP-catalog facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).mcp_catalog_facade_service,
        "MCP Catalog Facade Service",
    )


def oauth_facade_service_of(app_state: AppStateSliceMixin) -> OAuthFacadeService:
    """Resolve the OAuth facade service from its slice, or raise 503.

    Returns:
        The wired OAuth facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).oauth_facade_service,
        "OAuth Facade Service",
    )


def ontology_facade_service_of(
    app_state: AppStateSliceMixin,
) -> OntologyFacadeService:
    """Resolve the ontology facade service from its slice, or raise 503.

    Returns:
        The wired ontology facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).ontology_facade_service,
        "Ontology Facade Service",
    )


def quality_facade_service_of(app_state: AppStateSliceMixin) -> QualityFacadeService:
    """Resolve the quality facade service from its slice, or raise 503.

    Returns:
        The wired quality facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).quality_facade_service,
        "Quality Facade Service",
    )


def review_facade_service_of(app_state: AppStateSliceMixin) -> ReviewFacadeService:
    """Resolve the review facade service from its slice, or raise 503.

    Returns:
        The wired review facade service.
    """
    return _facade(
        app_state.slice(FacadesStateSlice).review_facade_service,
        "Review Facade Service",
    )
