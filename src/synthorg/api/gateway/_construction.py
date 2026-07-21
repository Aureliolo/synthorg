# module-kind: code
"""LLM gateway construction-phase state-slice wiring.

Builds the per-process token signer, the run-cost ledger and the request
pipeline, and commits them to the gateway state slice. The gateway is
built unconditionally (the collaborators are cheap and stateless); the
``providers.gateway_enabled`` setting gates behaviour per request, so the
route stays mounted and returns 503 while disabled rather than 404.
"""

from typing import TYPE_CHECKING

from synthorg.api.gateway.ledger import RunCostLedger
from synthorg.api.gateway.service import GatewayService
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_SERVICE_AUTO_WIRED

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState

logger = get_logger(__name__)


def wire_construction(app_state: AppState, _deps: ConstructionDeps) -> None:
    """Build + commit the gateway signer, ledger and pipeline."""
    signer = GatewaySigner.with_random_key()
    service = GatewayService(signer=signer, ledger=RunCostLedger())
    app_state.swap_slice(GatewayStateSlice(service=service, signer=signer))
    logger.info(API_SERVICE_AUTO_WIRED, service="llm_gateway")
