"""Pure authorisation predicates over an authenticated caller.

These depend only on the ``core.auth`` value types (``AuthenticatedUser``,
``HumanRole``), so both the HTTP controllers (via
``api.auth.controller_helpers``, which re-exports them) and the service
layer can share one definition without reaching up into the API layer.
"""

from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.auth.roles import HumanRole
from synthorg.core.types import NotBlankStr


def is_owner_or_ceo(
    requester: AuthenticatedUser, owner_user_id: NotBlankStr | None
) -> bool:
    """Return whether *requester* owns the resource or is the CEO.

    The shared owner-or-CEO authorisation predicate: a caller may act on
    a resource when its ``user_id`` matches the resource owner, or when
    the caller holds the CEO role (the org-wide override). An owner id of
    ``None`` (an unowned resource) is never owned by a specific user, so
    only the CEO override grants access.

    Args:
        requester: The authenticated caller.
        owner_user_id: The resource owner's user id, or ``None`` when the
            resource has no human owner.

    Returns:
        ``True`` when the caller owns the resource or is the CEO.
    """
    return requester.user_id == owner_user_id or requester.role is HumanRole.CEO
