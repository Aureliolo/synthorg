# module-kind: code
"""Boot wiring for the hiring pipeline.

Hiring has three callers (the scaler, the approvals controller finishing an
approved hire, and the staffing sweep asking for one), so it is declared in
its own right rather than inside any one of them: one owner, one
construction, and a capability the other two require rather than reach
through a sibling to find.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


async def wire_hiring(app_state: AppState) -> None:
    """Build the durable hiring pipeline and publish it.

    Idempotent for re-entered lifespans: returns early when the service is
    already published.

    Raises:
        SubsystemDeclinedError: When the approval store, the persistence
            backend or the settings resolver the pipeline needs is absent.
    """
    if app_state.slice(HrStateSlice).hiring_service is not None:
        return
    if app_state.slice(ApprovalStateSlice).store is None:
        msg = "no approval store; every hire is a gated decision"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; an in-flight hire must survive a restart"
        raise SubsystemDeclinedError(msg)
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        msg = (
            "no settings resolver; the model-spend profile that ranks a hire's "
            "proposed pairs is read from settings"
        )
        raise SubsystemDeclinedError(msg)
    catalogue = app_state.slice(ProvidersStateSlice).management
    if catalogue is None:
        msg = (
            "no provider management; a hire's model pair is proposed from the "
            "operator's configured providers"
        )
        raise SubsystemDeclinedError(msg)

    hiring = HiringService(
        registry=agent_registry_of(app_state),
        approval_store=app_state.slice(ApprovalStateSlice).store,
        # The resolver and the catalogue, not resolved values: both are read
        # when an approval is raised, so an operator who configures a provider
        # after boot gets it offered on the next hire with no restart.
        config_resolver=resolver,
        provider_catalogue=catalogue,
    )
    # Attachment is a hard prerequisite: without it the service is
    # non-durable, so a failure aborts wiring rather than publishing a
    # pipeline whose in-flight requests a restart would lose.
    hiring.attach_persistence(request_repo=persistence_of(app_state).hiring_requests)
    try:
        await hiring.hydrate()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # Only hydration is isolated: a failure leaves in-flight requests
        # unrestored (orphaned) rather than dormant, so the pipeline comes up
        # degraded instead of not at all.
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="hiring",
            note="hiring request hydration failed; in-flight requests may be orphaned",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    app_state.wire(HrStateSlice, hiring_service=hiring)
    logger.info(API_APP_STARTUP, service="hiring", note="wired (durable)")


__all__ = ["wire_hiring"]
