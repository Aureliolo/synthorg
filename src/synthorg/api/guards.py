"""Route guards for access control.

Guards read the authenticated user identity from ``connection.user``
(populated by the auth middleware) and check role-based permissions.
"""

from enum import StrEnum

from litestar.connection import ASGIConnection  # noqa: TC002
from litestar.exceptions import PermissionDeniedException

from synthorg.observability import get_logger
from synthorg.observability.events.api import API_GUARD_DENIED

logger = get_logger(__name__)


class HumanRole(StrEnum):
    """Recognised human roles for access control."""

    CEO = "ceo"
    MANAGER = "manager"
    BOARD_MEMBER = "board_member"
    PAIR_PROGRAMMER = "pair_programmer"
    OBSERVER = "observer"


_WRITE_ROLES: frozenset[HumanRole] = frozenset(
    {
        HumanRole.CEO,
        HumanRole.MANAGER,
        HumanRole.BOARD_MEMBER,
        HumanRole.PAIR_PROGRAMMER,
    }
)
_READ_ROLES: frozenset[HumanRole] = _WRITE_ROLES | frozenset({HumanRole.OBSERVER})


def _get_role(connection: ASGIConnection) -> HumanRole | None:  # type: ignore[type-arg]
    """Extract the human role from the authenticated user."""
    user = connection.scope.get("user")
    if user is not None and hasattr(user, "role"):
        try:
            return HumanRole(user.role)
        except ValueError:
            logger.warning(
                API_GUARD_DENIED,
                guard="_get_role",
                invalid_role=str(user.role),
                path=str(connection.url.path),
            )
            return None
    return None


def require_write_access(
    connection: ASGIConnection,  # type: ignore[type-arg]
    _: object,
) -> None:
    """Guard that allows only write-capable roles.

    Checks ``connection.user.role`` for ``ceo``, ``manager``,
    ``board_member``, or ``pair_programmer``.

    Args:
        connection: The incoming connection.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If the role is not permitted.
    """
    role = _get_role(connection)
    if role not in _WRITE_ROLES:
        logger.warning(
            API_GUARD_DENIED,
            guard="require_write_access",
            role=role,
            path=str(connection.url.path),
        )
        raise PermissionDeniedException(detail="Write access denied")


def require_read_access(
    connection: ASGIConnection,  # type: ignore[type-arg]
    _: object,
) -> None:
    """Guard that allows all recognised roles.

    Checks ``connection.user.role`` for any valid role
    including ``observer``.

    Args:
        connection: The incoming connection.
        _: Route handler (unused).

    Raises:
        PermissionDeniedException: If the role is not permitted.
    """
    role = _get_role(connection)
    if role not in _READ_ROLES:
        logger.warning(
            API_GUARD_DENIED,
            guard="require_read_access",
            role=role,
            path=str(connection.url.path),
        )
        raise PermissionDeniedException(detail="Read access denied")
