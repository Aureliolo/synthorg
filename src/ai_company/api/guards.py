"""Route guards for access control.

.. warning:: **Security Stub (M6)**

   These guards check the ``X-Human-Role`` header, which is
   **self-asserted by the caller** — there is no signature
   verification, session token, or JWT.  This is intentional for
   the M6 milestone scope.  **Real authentication and authorization
   (pre-shared API key, JWT, or OAuth) will be implemented in M7
   (issue scope: security & HR).**

   Until M7, the API should only be exposed on trusted networks or
   behind a reverse proxy that enforces authentication.
"""

from enum import StrEnum

from litestar.connection import ASGIConnection  # noqa: TC002
from litestar.exceptions import PermissionDeniedException

from ai_company.observability import get_logger
from ai_company.observability.events.api import API_GUARD_DENIED

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


def _get_role(connection: ASGIConnection) -> str | None:  # type: ignore[type-arg]
    """Extract the human role from the request header."""
    value = connection.headers.get("x-human-role")
    if value is not None:
        return value.strip().lower()
    return None


def require_write_access(
    connection: ASGIConnection,  # type: ignore[type-arg]
    _: object,
) -> None:
    """Guard that allows only write-capable roles.

    Checks the ``X-Human-Role`` header for ``ceo``, ``manager``,
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

    Checks the ``X-Human-Role`` header for any valid role
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
