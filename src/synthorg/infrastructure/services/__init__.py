# module-kind: code
"""Infrastructure facades for the MCP handler layer.

Thin per-subdomain facades used by the infrastructure MCP tools
(settings, providers, backup, users, projects, requests, setup,
simulations, template packs, audit, events, integration health).
Each facade wraps the already-attached AppState primitive and lifts
the common audit-log pattern into a single owner.

For operations whose underlying primitive does not yet expose the
required method the facade raises
:class:`~synthorg.communication.mcp_errors.CapabilityNotSupportedError`,
which the MCP handler translates to a typed ``not_supported`` envelope.
This keeps the distinction between "handler wired, primitive
capability missing" and "handler unwired" observable on the wire.
"""

from synthorg.infrastructure.services._read_facades import (
    BackupFacadeService,
    ProviderReadService,
    SettingsReadService,
    UserFacadeService,
)
from synthorg.infrastructure.services._registries import (
    ProjectFacadeService,
    RequestsFacadeService,
    TemplatePackFacadeService,
)
from synthorg.infrastructure.services._status_facades import (
    AuditReadService,
    EventsReadService,
    IntegrationHealthFacadeService,
    SetupFacadeService,
    SimulationFacadeService,
)

__all__ = [
    "AuditReadService",
    "BackupFacadeService",
    "EventsReadService",
    "IntegrationHealthFacadeService",
    "ProjectFacadeService",
    "ProviderReadService",
    "RequestsFacadeService",
    "SettingsReadService",
    "SetupFacadeService",
    "SimulationFacadeService",
    "TemplatePackFacadeService",
    "UserFacadeService",
]
