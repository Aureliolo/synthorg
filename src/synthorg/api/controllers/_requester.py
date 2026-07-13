"""Shared controller helper: resolve the acting requester from request state.

Lives in its own module so controllers that record an actor on an audited write
(tasks, projects, ...) share one implementation instead of reaching across a
sibling controller's private boundary.
"""

from litestar.datastructures import State

from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AUTH_FALLBACK

logger = get_logger(__name__)


def extract_requester(state: State) -> str:
    """Resolve the requester identity from the authenticated user.

    Falls back to ``"api"`` when the connection carries no user (e.g. in tests
    without auth middleware). Logs a warning on fallback so an auth
    misconfiguration is visible in production.

    Args:
        state: The request-scoped application state.

    Returns:
        The authenticated user id, or ``"api"`` when none is present.
    """
    user = getattr(state, "_connection_user", None)
    if user is not None and hasattr(user, "user_id"):
        return str(user.user_id)
    logger.warning(
        API_AUTH_FALLBACK,
        note="No authenticated user found, falling back to 'api'",
    )
    return "api"
