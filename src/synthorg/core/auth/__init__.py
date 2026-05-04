"""Authentication domain types.

Lives in ``core`` (not ``api``) so persistence repositories, engine
modules, and other non-HTTP code can import auth types without
crossing a layer boundary into ``synthorg.api``. The HTTP-coupled
``AuthService`` and ``WsTicketStore`` remain in
``synthorg.api.auth`` because they bind to JWT issuer/audience
constants and Litestar-specific WebSocket primitives.
"""

from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import (
    ApiKey,
    AuthenticatedUser,
    AuthMethod,
    OrgRole,
    User,
)
from synthorg.core.auth.refresh_record import (
    RefreshConsumeOutcome,
    RefreshRecord,
    RefreshRejectReason,
)
from synthorg.core.auth.roles import HumanRole
from synthorg.core.auth.session import Session

__all__ = [
    "ApiKey",
    "AuthConfig",
    "AuthMethod",
    "AuthenticatedUser",
    "HumanRole",
    "OrgRole",
    "RefreshConsumeOutcome",
    "RefreshRecord",
    "RefreshRejectReason",
    "Session",
    "User",
]
