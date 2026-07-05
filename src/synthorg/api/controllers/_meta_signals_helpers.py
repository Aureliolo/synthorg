"""Shared signals-service resolution for the meta controllers."""

from synthorg.api.state import AppState
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.state import MetaStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE

logger = get_logger(__name__)


def require_signals_service(app_state: AppState, msg: str) -> SignalsService:
    """Resolve the signals service, logging + raising 503 when unwired.

    Shared by the meta ``get_signals`` and ``chat`` handlers so both emit
    the same ``META_CHAT_DEPENDENCY_UNAVAILABLE`` warning; *msg* is the
    call-site-specific operator message carried on the raised error.

    Args:
        app_state: Application state carrying the meta state slice.
        msg: Operator-facing message set on the raised 503.

    Returns:
        The wired signals service.

    Raises:
        ServiceUnavailableError: When no signals service is wired.
    """
    signals_service = app_state.slice(MetaStateSlice).signals_service
    if signals_service is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="signals_service",
            hint="SignalsService must be wired during AppState startup.",
        )
        raise ServiceUnavailableError(msg)
    return signals_service
