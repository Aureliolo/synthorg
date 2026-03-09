"""Route guards for access control.

Stub guards that check the ``X-Human-Role`` header.  Real
authentication and authorization is implemented in M7.
"""

from litestar.connection import ASGIConnection  # noqa: TC002
from litestar.exceptions import PermissionDeniedException

from ai_company.observability import get_logger
from ai_company.observability.events.api import API_GUARD_DENIED

logger = get_logger(__name__)

_WRITE_ROLES: frozenset[str] = frozenset(
    {"ceo", "manager", "board_member", "pair_programmer"}
)
_READ_ROLES: frozenset[str] = _WRITE_ROLES | frozenset({"observer"})


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
