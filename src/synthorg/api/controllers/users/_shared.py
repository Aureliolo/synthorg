"""Shared service factory, response DTO, and lookup for user controllers."""

from typing import LiteralString

from litestar.datastructures import State
from pydantic import AwareDatetime, BaseModel, ConfigDict

from synthorg.api.auth.user_service import UserService
from synthorg.api.responses import require_resource_or_404
from synthorg.core.auth.models import User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.persistence.state import persistence_of


def _service(state: State) -> UserService:
    """Build the per-request :class:`UserService`.

    Threads the refresh-token repo so ``delete()`` can explicitly
    revoke outstanding refresh tokens before the DB delete as
    defense-in-depth (CFG-1 audit). Sessions, api_keys, and
    refresh_tokens are all also removed by the schema's
    ``ON DELETE CASCADE`` on ``user_id`` when the user row goes
    away -- the explicit revocation runs first so tokens stop
    minting access tokens immediately.

    Returns:
        ``UserService`` instance.
    """
    persistence = persistence_of(state.app_state)
    return UserService(
        repo=persistence.users,
        refresh_tokens=persistence.refresh_tokens,
    )


class UserResponse(BaseModel):
    """Public user representation (no password hash)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    username: NotBlankStr
    role: HumanRole
    must_change_password: bool
    org_roles: tuple[str, ...] = ()
    scoped_departments: tuple[str, ...] = ()
    created_at: AwareDatetime
    updated_at: AwareDatetime


def _to_response(user: User) -> UserResponse:
    """Map a ``User`` domain model to the public ``UserResponse`` DTO.

    Returns:
        ``UserResponse`` instance.
    """
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        must_change_password=user.must_change_password,
        org_roles=tuple(r.value for r in user.org_roles),
        scoped_departments=user.scoped_departments,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


async def _get_user_or_404(
    service: UserService,
    user_id: str,
    *,
    operation: LiteralString,
) -> User:
    """Fetch a user by ID, raising NotFoundError if missing.

    ``operation`` discriminates the failing endpoint in the audit log
    (``"read"`` / ``"update_user_role"`` / ``"delete_user"`` /
    ``"grant_org_role"`` / ``"revoke_org_role"``) so the not-found
    emissions are not all stamped as ``"read"``.

    Returns:
        ``User`` instance.
    """
    return require_resource_or_404(
        await service.get(NotBlankStr(user_id)),
        resource_type="User",
        identifier=user_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation=operation,
    )
