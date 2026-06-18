"""Route guards for access control.

Guards read the authenticated user identity from ``connection.user``
(populated by the auth middleware) and check role-based permissions.

The ``require_roles`` factory creates guards for arbitrary role sets.
Pre-built constants cover common patterns::

    require_ceo              -- CEO only
    require_ceo_or_manager   -- CEO or Manager
    require_approval_roles   -- CEO, Manager, or Board Member
"""

from collections.abc import Callable

from litestar.connection import ASGIConnection
from litestar.datastructures import State
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import HTTPRouteHandler

from synthorg.core.auth.roles import HumanRole
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_GUARD_DENIED

logger = get_logger(__name__)

# Guards run on REST routes only; litestar binds ``Request`` to
# ``ASGIConnection[HTTPRouteHandler, ...]``, so pinning the handler slot
# keeps the param type Any-free while still accepting the request the
# framework passes in (and the ``Request`` fakes the guard tests build).
type GuardConnection = ASGIConnection[HTTPRouteHandler, object, object, State]


# --- Role sets --------------------------------------------------------

_WRITE_ROLES: frozenset[HumanRole] = frozenset(
    {
        HumanRole.CEO,
        HumanRole.MANAGER,
        HumanRole.PAIR_PROGRAMMER,
    }
)
_READ_ROLES: frozenset[HumanRole] = _WRITE_ROLES | frozenset(
    {HumanRole.OBSERVER, HumanRole.BOARD_MEMBER},
)


def _get_role(
    connection: GuardConnection,
) -> HumanRole | None:
    """Extract the human role from the authenticated user.

    Returns:
        The ``HumanRole`` value when present, ``None`` otherwise.
    """
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


def has_write_role(role: HumanRole) -> bool:
    """Return True if the role grants write access.

    Use this for inline role checks instead of importing ``_WRITE_ROLES``
    directly.  The write set includes CEO, Manager, and Pair Programmer.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    return role in _WRITE_ROLES


def require_write_access(
    connection: GuardConnection,
    _: object,
) -> None:
    """Guard that allows only write-capable human roles.

    Checks ``connection.user.role`` for ``ceo``, ``manager``,
    or ``pair_programmer``.  Board members are excluded (they
    may only observe and approve).  The ``system`` role is
    intentionally excluded -- use ``require_roles()`` with the
    desired roles for endpoints the CLI needs to reach.

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
    connection: GuardConnection,
    _: object,
) -> None:
    """Guard that allows all human roles (excludes SYSTEM).

    Checks ``connection.user.role`` for any human role
    including ``observer`` and ``board_member``.  The internal
    ``system`` role is excluded -- use ``require_roles()`` for
    endpoints the CLI needs to reach.

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


# --- Guard factory ----------------------------------------------------


def require_roles(
    *roles: HumanRole,
) -> Callable[[GuardConnection, object], None]:
    """Create a guard that allows only the specified roles.

    Args:
        *roles: One or more ``HumanRole`` members to permit.

    Returns:
        A guard function compatible with Litestar's guard protocol.

    Raises:
        ValueError: If no roles are provided.
    """
    if not roles:
        msg = "require_roles() requires at least one role"
        raise ValueError(msg)

    allowed = frozenset(roles)
    label = ",".join(sorted(r.value for r in allowed))

    def guard(
        connection: GuardConnection,
        _: object,
    ) -> None:
        """Handle guard.

        Raises:
            PermissionDeniedException: Raised on the corresponding failure path.
        """
        role = _get_role(connection)
        if role not in allowed:
            logger.warning(
                API_GUARD_DENIED,
                guard=f"require_roles({label})",
                role=role,
                path=str(connection.url.path),
            )
            raise PermissionDeniedException(detail="Access denied")

    guard.__name__ = f"require_roles({label})"
    guard.__qualname__ = f"require_roles({label})"
    return guard


# --- Named guard constants --------------------------------------------

require_ceo = require_roles(HumanRole.CEO)
"""Guard allowing only the CEO role."""

require_ceo_or_manager = require_roles(HumanRole.CEO, HumanRole.MANAGER)
"""Guard allowing CEO or Manager roles."""

require_approval_roles = require_roles(
    HumanRole.CEO,
    HumanRole.MANAGER,
    HumanRole.BOARD_MEMBER,
)
"""Guard allowing roles that can approve or reject actions."""


# --- Org-level permission guards (OrgRole) ----------------------------
# String constants matching OrgRole enum values. Kept as bare strings
# so this module does not pull in ``synthorg.core.auth.models`` (which
# would force every guard-importing controller to instantiate the full
# user/role model graph at import time).
_ORG_ROLE_OWNER = "owner"
_ORG_ROLE_EDITOR = "editor"
_ORG_ROLE_DEPARTMENT_ADMIN = "department_admin"


def _get_org_roles(
    connection: GuardConnection,
) -> tuple[str, ...]:
    """Extract OrgRole string values from the authenticated user.

    Returns:
        Tuple of the declared element types.
    """
    user = connection.scope.get("user")
    if user is not None and hasattr(user, "org_roles"):
        return tuple(r.value if hasattr(r, "value") else str(r) for r in user.org_roles)
    return ()


def _get_scoped_departments(
    connection: GuardConnection,
) -> tuple[str, ...]:
    """Extract scoped departments from the authenticated user.

    Returns:
        Tuple of the declared element types.
    """
    user = connection.scope.get("user")
    if user is not None and hasattr(user, "scoped_departments"):
        return tuple(str(d) for d in user.scoped_departments)
    return ()


def require_org_mutation(
    department_param: str | None = None,
) -> Callable[[GuardConnection, object], None]:
    """Guard factory for org config mutations.

    Access is granted if the user has one of:

    - ``OrgRole.OWNER`` -- always allowed
    - ``OrgRole.EDITOR`` -- always allowed
    - ``OrgRole.DEPARTMENT_ADMIN`` -- allowed only when the
      target department (read from the path parameter named
      *department_param*) is in the user's ``scoped_departments``

    A user with no ``org_roles`` is denied: organisation-level roles
    are the sole authority for org-config mutation.

    Args:
        department_param: Path parameter name containing the target
            department (e.g. ``"name"``).  ``None`` skips department
            scope checking (company-level endpoints).

    Returns:
        A guard function compatible with Litestar's guard protocol.

    Raises:
        PermissionDeniedException: Raised on the corresponding failure path.
    """

    def guard(
        connection: GuardConnection,
        _: object,
    ) -> None:
        """Handle guard.

        Raises:
            PermissionDeniedException: Raised on the corresponding failure path.
        """
        org_roles = _get_org_roles(connection)

        # Owner and editor always allowed
        if _ORG_ROLE_OWNER in org_roles or _ORG_ROLE_EDITOR in org_roles:
            return

        # Department admin: check scope
        if _ORG_ROLE_DEPARTMENT_ADMIN in org_roles:
            if department_param is None:
                # Company-level endpoint -- dept_admin cannot modify
                logger.warning(
                    API_GUARD_DENIED,
                    guard="require_org_mutation(dept_admin_no_scope)",
                    path=str(connection.url.path),
                )
                raise PermissionDeniedException(
                    detail="Department admins cannot modify company-level settings",
                )
            target_dept = connection.path_params.get(department_param, "")
            scoped = _get_scoped_departments(connection)
            if target_dept.lower() in (d.lower() for d in scoped):
                return
            logger.warning(
                API_GUARD_DENIED,
                guard="require_org_mutation(dept_admin_out_of_scope)",
                target_department=target_dept,
                scoped_departments=scoped,
                path=str(connection.url.path),
            )
            raise PermissionDeniedException(
                detail=f"Department admin access denied for {target_dept!r}",
            )

        # Viewer or unrecognised role
        logger.warning(
            API_GUARD_DENIED,
            guard="require_org_mutation(insufficient_org_role)",
            org_roles=org_roles,
            path=str(connection.url.path),
        )
        raise PermissionDeniedException(detail="Org mutation access denied")

    guard.__name__ = "require_org_mutation"
    guard.__qualname__ = "require_org_mutation"
    return guard
