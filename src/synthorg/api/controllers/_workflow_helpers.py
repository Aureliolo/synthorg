"""Shared helpers for controllers that derive an audit actor from auth.

Identity (``user_id``) is exposed via
:func:`synthorg.api.auth.context.get_authenticated_user_id`; this module
adds the audit-row helpers that wrap the bound user as a
:class:`ProviderAuditActor`. Background tasks that legitimately run
without an authenticated user opt into the ``api`` sentinel via the
:data:`BACKGROUND_AUDIT_ACTOR` constant.
"""

from typing import TYPE_CHECKING, Final

from synthorg.api.auth.context import get_authenticated_user

if TYPE_CHECKING:
    from synthorg.api.dto_provider_capabilities import ProviderAuditActor


def audit_actor_from_context() -> ProviderAuditActor:
    """Build a :class:`ProviderAuditActor` from the bound authenticated user.

    Raises:
        AuthContextMissingError: When no authenticated user is bound to
            the request scope (middleware misconfiguration). Background
            paths that legitimately have no user should reference
            :data:`BACKGROUND_AUDIT_ACTOR` instead of calling this.

    Returns:
        ``ProviderAuditActor`` instance.
    """
    from synthorg.api.dto_provider_capabilities import (  # noqa: PLC0415
        ProviderAuditActor,
    )

    user = get_authenticated_user()
    return ProviderAuditActor(id=user.user_id, label=user.username)


def _build_background_actor() -> ProviderAuditActor:
    """Build the background actor.

    Returns:
        ``ProviderAuditActor`` instance.
    """
    from synthorg.api.dto_provider_capabilities import (  # noqa: PLC0415
        ProviderAuditActor,
    )

    return ProviderAuditActor(id="api", label="api")


# Sentinel actor for background paths that legitimately have no
# authenticated user (scheduled jobs, startup probes). Callers must
# reference this constant explicitly so the ``api`` actor cannot be
# emitted by accident from a request-coupled code path.
BACKGROUND_AUDIT_ACTOR: Final[ProviderAuditActor] = _build_background_actor()
