# module-kind: code
"""Route-mount predicates for conditionally-registered controllers.

The discovery-based composition root evaluates a
:class:`~synthorg._core.features.ControllerRegistration` predicate against
the live :class:`~synthorg.api.state.AppState` at route-assembly time; a
controller whose predicate returns ``False`` stays unmounted, preserving
the historic behaviour where a disabled or unwired subsystem's routes do
not exist (404) rather than 503 on every dashboard poll.

Each predicate mirrors the readiness gate ``api.app`` applied inline before
the composition-root refactor: the integration controllers gate on
``integrations.enabled`` plus their own collaborators, the a2a controllers
gate on the committed a2a state-slice build outcome, and the optional engine
controllers gate on their work-entry adapter being wired.
"""

from typing import TYPE_CHECKING

from synthorg.a2a.state import A2aStateSlice
from synthorg.communication.state import CommunicationStateSlice
from synthorg.engine.state import EngineStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.persistence.state import PersistenceStateSlice

if TYPE_CHECKING:
    from synthorg.api.state import AppState


def _integrations_enabled(app_state: AppState) -> bool:
    """Report whether the integrations subsystem is enabled in config.

    Returns:
        ``True`` when ``integrations.enabled`` is set in the resolved config.
    """
    return app_state.config.integrations.enabled


def connections_controller_ready(app_state: AppState) -> bool:
    """Mount the connections controller when the connection catalog is wired.

    Returns:
        ``True`` when integrations are enabled and the catalog is present.
    """
    return (
        _integrations_enabled(app_state)
        and app_state.slice(IntegrationsStateSlice).connection_catalog is not None
    )


def integration_health_controller_ready(app_state: AppState) -> bool:
    """Mount the integration-health controller (shares the catalog gate).

    Returns:
        ``True`` when integrations are enabled and the catalog is present.
    """
    return (
        _integrations_enabled(app_state)
        and app_state.slice(IntegrationsStateSlice).connection_catalog is not None
    )


def oauth_controller_ready(app_state: AppState) -> bool:
    """Mount the OAuth controller when the catalog and persistence are wired.

    Returns:
        ``True`` when integrations are enabled and both the connection
        catalog and a connected persistence backend are present.
    """
    return (
        _integrations_enabled(app_state)
        and app_state.slice(IntegrationsStateSlice).connection_catalog is not None
        and app_state.slice(PersistenceStateSlice).backend is not None
    )


def webhooks_controller_ready(app_state: AppState) -> bool:
    """Mount the webhooks controller when the catalog and message bus are wired.

    Returns:
        ``True`` when integrations are enabled and both the connection
        catalog and the message bus are present.
    """
    return (
        _integrations_enabled(app_state)
        and app_state.slice(IntegrationsStateSlice).connection_catalog is not None
        and app_state.slice(CommunicationStateSlice).message_bus is not None
    )


def mcp_catalog_controller_ready(app_state: AppState) -> bool:
    """Mount the MCP-catalog controller when its catalog service is wired.

    Returns:
        ``True`` when integrations are enabled and the catalog service is
        present.
    """
    return (
        _integrations_enabled(app_state)
        and app_state.slice(IntegrationsStateSlice).mcp_catalog_service is not None
    )


def tunnel_controller_ready(app_state: AppState) -> bool:
    """Mount the tunnel controller when the tunnel provider is wired.

    Returns:
        ``True`` when integrations are enabled and the tunnel provider is
        present.
    """
    return (
        _integrations_enabled(app_state)
        and app_state.slice(IntegrationsStateSlice).tunnel_provider is not None
    )


def a2a_well_known_ready(app_state: AppState) -> bool:
    """Mount the a2a well-known card controller when the card builder is wired.

    The a2a state slice commits a card builder only when a2a is enabled and
    the build chain succeeds, so this single check captures the historic
    enabled-and-built gate.

    Returns:
        ``True`` when the a2a card builder is present.
    """
    return app_state.slice(A2aStateSlice).card_builder is not None


def a2a_gateway_ready(app_state: AppState) -> bool:
    """Mount the a2a JSON-RPC gateway when the outbound client is wired.

    The outbound client commits only when a2a and integrations are enabled
    and the connection catalog is present, so this check captures the
    historic gate.

    Returns:
        ``True`` when the a2a outbound client is present.
    """
    return app_state.slice(A2aStateSlice).client is not None


def objective_controller_ready(app_state: AppState) -> bool:
    """Mount the objective controller when its work-entry adapter is wired.

    The adapter is wired during startup, after route assembly, so on the
    standard boot path this is ``False`` at mount time (the controller is
    latent until a deployment wires the adapter at construction).

    Returns:
        ``True`` when the objective work-entry adapter is present.
    """
    return app_state.slice(EngineStateSlice).objective_entry_adapter is not None


def brownfield_controller_ready(app_state: AppState) -> bool:
    """Mount the brownfield controller when its work-entry adapter is wired.

    The adapter is wired during startup, after route assembly, so on the
    standard boot path this is ``False`` at mount time (the controller is
    latent until a deployment wires the adapter at construction).

    Returns:
        ``True`` when the brownfield work-entry adapter is present.
    """
    return app_state.slice(EngineStateSlice).brownfield_entry_adapter is not None
