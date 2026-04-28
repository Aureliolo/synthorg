"""Shared helpers for workflow controllers."""

from typing import TYPE_CHECKING, Any

from synthorg.api.auth.models import AuthenticatedUser
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AUTH_USER_FALLBACK

if TYPE_CHECKING:
    from litestar import Request

    from synthorg.api.dto_provider_capabilities import ProviderAuditActor

logger = get_logger(__name__)


def get_auth_user_id(request: Request[Any, Any, Any]) -> str:
    """Extract the authenticated user ID from a request.

    Args:
        request: The incoming Litestar request.

    Returns:
        The user ID string, or ``"api"`` when no
        ``AuthenticatedUser`` is in scope.
    """
    auth_user = request.scope.get("user")
    if isinstance(auth_user, AuthenticatedUser):
        return auth_user.user_id
    logger.debug(
        API_AUTH_USER_FALLBACK,
        reason="no AuthenticatedUser in scope",
        path=request.url.path,
    )
    return "api"


def request_audit_actor(
    request: Request[Any, Any, Any],
) -> ProviderAuditActor:
    """Derive a ``ProviderAuditActor`` from the request scope.

    Falls back to the ``api`` sentinel id (mirroring
    :func:`get_auth_user_id`) when no ``AuthenticatedUser`` is
    bound, so audit rows are still emitted from background paths
    without leaking that as a privileged actor.
    """
    from synthorg.api.dto_provider_capabilities import (  # noqa: PLC0415
        ProviderAuditActor,
    )

    auth_user = request.scope.get("user")
    if isinstance(auth_user, AuthenticatedUser):
        return ProviderAuditActor(
            id=auth_user.user_id,
            label=auth_user.username,
        )
    logger.debug(
        API_AUTH_USER_FALLBACK,
        reason="no AuthenticatedUser in scope",
        path=request.url.path,
    )
    return ProviderAuditActor(id="api", label="api")
