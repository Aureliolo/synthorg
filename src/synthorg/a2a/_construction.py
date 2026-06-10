# module-kind: code
"""A2A feature construction-phase state-slice wiring.

Builds the a2a collaborators and commits them to the a2a state slice on full
success (the historic build-all-then-commit guard): a partial failure leaves
the slice empty so the well-known + gateway controller predicates keep both
unmounted. The heavy collaborators are imported lazily so a boot with a2a
disabled never pays their import cost.
"""

from typing import TYPE_CHECKING

from synthorg.a2a.state import A2aStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_float

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState

logger = get_logger(__name__)


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Build + commit the a2a collaborators when a2a is enabled."""
    effective_config = deps.effective_config
    if not effective_config.a2a.enabled:
        return
    connection_catalog = deps.integrations.connection_catalog
    a2a_card_builder = None
    a2a_peer_registry = None
    a2a_client_obj = None
    try:
        from synthorg.a2a.agent_card import AgentCardBuilder  # noqa: PLC0415
        from synthorg.a2a.models import A2AAuthSchemeInfo  # noqa: PLC0415

        auth_schemes = (
            A2AAuthSchemeInfo(scheme=str(effective_config.a2a.auth.inbound_scheme)),
        )
        a2a_card_builder = AgentCardBuilder(default_auth_schemes=auth_schemes)

        # Outbound client + JSON-RPC gateway need the connection catalog and
        # integrations enabled.
        if effective_config.integrations.enabled and connection_catalog is not None:
            import httpx  # noqa: PLC0415

            from synthorg.a2a.client import A2AClient  # noqa: PLC0415
            from synthorg.a2a.peer_registry import PeerRegistry  # noqa: PLC0415
            from synthorg.tools.network_validator import NetworkPolicy  # noqa: PLC0415

            a2a_peer_registry = PeerRegistry()
            a2a_client_timeout = float(
                resolve_init_value(
                    SettingNamespace.A2A,
                    "client_timeout_seconds",
                    parse=parse_float,
                ).value
            )
            a2a_http_client = httpx.AsyncClient(timeout=a2a_client_timeout)
            a2a_client_obj = A2AClient(
                connection_catalog,
                network_validator=NetworkPolicy(),
                http_client=a2a_http_client,
                timeout_seconds=a2a_client_timeout,
            )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            note="A2A gateway auto-wire failed (non-fatal)",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    else:
        # Commit only on full success; partial failures land above with the
        # slice still empty so the predicates keep both controllers unmounted.
        app_state.swap_slice(
            A2aStateSlice(
                card_builder=a2a_card_builder,
                client=a2a_client_obj,
                peer_registry=a2a_peer_registry,
            )
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="a2a_gateway")
