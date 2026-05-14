"""User management controller -- CEO-only CRUD for human users."""

import uuid
from datetime import UTC, datetime
from typing import Any, Final, LiteralString

from litestar import Controller, Request, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.api.auth.user_service import UserService
from synthorg.api.cursor import decode_keyset_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_ceo
from synthorg.api.pagination import CursorLimit, CursorParam, encode_keyset_meta
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import AuthenticatedUser, OrgRole, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_USER_SAVE_FAILED,
    API_VALIDATION_FAILED,
)
from synthorg.observability.events.security import (
    SECURITY_PERMISSION_GRANTED,
    SECURITY_PERMISSION_REVOKED,
)
from synthorg.persistence.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _service(state: State) -> UserService:
    """Build the per-request :class:`UserService`.

    Threads the refresh-token repo so ``delete()`` can explicitly
    revoke outstanding refresh tokens before the DB delete as
    defense-in-depth (CFG-1 audit). Sessions, api_keys, and
    refresh_tokens are all also removed by the schema's
    ``ON DELETE CASCADE`` on ``user_id`` when the user row goes
    away -- the explicit revocation runs first so tokens stop
    minting access tokens immediately.
    """
    persistence = state.app_state.persistence
    return UserService(
        repo=persistence.users,
        refresh_tokens=persistence.refresh_tokens,
    )


# Derive from AuthConfig default to prevent silent divergence.
_MIN_PASSWORD_LENGTH: int = AuthConfig.model_fields["min_password_length"].default

# Roles that cannot be assigned via the user management API.
_FORBIDDEN_ROLES: frozenset[HumanRole] = frozenset({HumanRole.SYSTEM})


# -- Request / Response DTOs -------------------------------------------


class CreateUserRequest(BaseModel):
    """Request body for creating a new user."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    username: NotBlankStr = Field(max_length=128)
    password: NotBlankStr = Field(max_length=128)
    role: HumanRole


class UpdateUserRoleRequest(BaseModel):
    """Request body for updating a user's role."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role: HumanRole


class GrantOrgRoleRequest(BaseModel):
    """Request body for granting an org-level role."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role: OrgRole
    scoped_departments: tuple[NotBlankStr, ...] = ()


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
    """Map a ``User`` domain model to the public ``UserResponse`` DTO."""
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


# -- Validation helpers ------------------------------------------------


def _validate_assignable_role(role: HumanRole) -> None:
    """Reject roles that cannot be assigned via the API."""
    if role in _FORBIDDEN_ROLES:
        msg = f"Cannot assign role: {role.value}"
        logger.warning(API_VALIDATION_FAILED, reason=msg)
        raise ValidationError(msg)


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
    """
    return require_resource_or_404(
        await service.get(NotBlankStr(user_id)),
        resource_type="User",
        identifier=user_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation=operation,
    )


# -- Controller --------------------------------------------------------


class UserController(Controller):
    """CEO-only endpoints for managing human user accounts.

    All endpoints require the CEO role.
    """

    path = "/users"
    tags = ("users",)
    guards = [require_ceo]  # noqa: RUF012

    @post(
        status_code=201,
        guards=[
            per_op_rate_limit_from_policy("users.create", key="user"),
        ],
    )
    async def create_user(
        self,
        state: State,
        data: CreateUserRequest,
    ) -> ApiResponse[UserResponse]:
        """Create a new user account.

        Args:
            state: Application state.
            data: User creation payload.

        Returns:
            Created user response.

        Raises:
            ValidationError: If the role is SYSTEM or password
                is too short.
            ConflictError: If username is taken or a second CEO is
                requested.
        """
        app_state: AppState = state.app_state

        _validate_assignable_role(data.role)

        if len(data.password) < _MIN_PASSWORD_LENGTH:
            msg = f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"
            logger.warning(API_VALIDATION_FAILED, reason=msg)
            raise ValidationError(msg)

        now = datetime.now(UTC)
        password_hash = await app_state.auth_service.hash_password_async(
            data.password,
        )
        user = User(
            id=str(uuid.uuid4()),
            username=data.username,
            password_hash=password_hash,
            role=data.role,
            must_change_password=True,
            created_at=now,
            updated_at=now,
        )
        try:
            await _service(state).create(user)
        except ConstraintViolationError as exc:
            if exc.constraint == USERS_USERNAME_UNIQUE:
                msg = f"Username already taken: {data.username}"
            elif exc.constraint == IDX_SINGLE_CEO:
                msg = "A CEO user already exists"
            else:
                logger.error(
                    API_USER_SAVE_FAILED,
                    user_id=user.id,
                    intent="create_user",
                    constraint=exc.constraint,
                )
                raise
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg) from exc
        except QueryError:
            logger.error(
                API_USER_SAVE_FAILED,
                user_id=user.id,
                intent="create_user",
            )
            raise

        return ApiResponse(data=_to_response(user))

    @get()
    async def list_users(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[UserResponse]:
        """List human users with keyset-based cursor pagination.

        Sorted by user ``id`` so cursor pages stay stable under
        concurrent inserts and deletes -- offset-based pagination
        could duplicate or skip rows when the visible window shifts
        between fetches.  The cursor encodes the last ``id`` returned;
        the next page reads ``WHERE id > after_id``.  Pagination is
        pushed into the SQL layer via
        :meth:`UserService.list_users_page` (no COUNT round-trip per
        request -- ``pagination.total`` is ``null``).

        Args:
            state: Application state.
            cursor: Opaque keyset cursor from a previous page.
            limit: Page size (default 50, max defined by ``MAX_LIMIT``).

        Returns:
            Paginated response of user entries.

        Raises:
            InvalidCursorError: HTTP 400 -- malformed, tampered, or
                signed by a different secret.
        """
        app_state: AppState = state.app_state
        after_id = (
            decode_keyset_cursor(cursor, secret=app_state.cursor_secret)
            if cursor is not None
            else None
        )
        page, has_more = await _service(state).list_users_page(
            after_id=after_id,
            limit=limit,
        )
        next_after_key = page[-1].id if has_more and page else None
        meta = encode_keyset_meta(
            next_after_key=next_after_key,
            has_more=has_more,
            limit=limit,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(
            data=tuple(_to_response(u) for u in page),
            pagination=meta,
        )

    @get("/{user_id:str}")
    async def get_user(
        self,
        state: State,
        user_id: PathId,
    ) -> ApiResponse[UserResponse]:
        """Get a user by ID.

        Args:
            state: Application state.
            user_id: User identifier.

        Returns:
            User response.

        Raises:
            NotFoundError: If the user is not found.
        """
        user = await _get_user_or_404(_service(state), user_id, operation="read")
        return ApiResponse(data=_to_response(user))

    @patch(
        "/{user_id:str}",
        guards=[
            per_op_rate_limit_from_policy("users.update_role", key="user"),
        ],
    )
    async def update_user_role(
        self,
        state: State,
        user_id: PathId,
        data: UpdateUserRoleRequest,
    ) -> ApiResponse[UserResponse]:
        """Update a user's role.

        Args:
            state: Application state.
            user_id: User identifier.
            data: Role update payload.

        Returns:
            Updated user response.

        Raises:
            NotFoundError: If the user is not found.
            ValidationError: If the target role is SYSTEM.
            ConflictError: If the target user is the system user,
                changing the only CEO's role, or assigning a
                second CEO.
        """
        service = _service(state)

        _validate_assignable_role(data.role)
        user = await _get_user_or_404(service, user_id, operation="update_user_role")

        if user.role == HumanRole.SYSTEM:
            msg = "Cannot modify the system user"
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg)

        now = datetime.now(UTC)
        updated = user.model_copy(
            update={"role": data.role, "updated_at": now},
        )
        try:
            await service.save_update(
                updated,
                intent="update_user_role",
                old_role=user.role.value,
                new_role=data.role.value,
            )
        except ConstraintViolationError as exc:
            if exc.constraint == LAST_CEO_TRIGGER:
                msg = "Cannot change the only CEO's role"
            elif exc.constraint == IDX_SINGLE_CEO:
                msg = "A CEO user already exists"
            else:
                logger.error(
                    API_USER_SAVE_FAILED,
                    user_id=user.id,
                    intent="update_user_role",
                    constraint=exc.constraint,
                )
                raise
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg) from exc
        except QueryError:
            logger.error(
                API_USER_SAVE_FAILED,
                user_id=user.id,
                intent="update_user_role",
            )
            raise

        return ApiResponse(data=_to_response(updated))

    @delete(
        "/{user_id:str}",
        status_code=HTTP_204_NO_CONTENT,
        guards=[
            per_op_rate_limit_from_policy("users.delete", key="user"),
        ],
    )
    async def delete_user(
        self,
        state: State,
        user_id: PathId,
        request: Request[Any, Any, Any],
    ) -> None:
        """Delete a user account.

        Args:
            state: Application state.
            user_id: User identifier.
            request: The incoming HTTP request.

        Raises:
            NotFoundError: If the user is not found.
            ConflictError: If attempting to delete your own account,
                the system user, or the CEO.
        """
        service = _service(state)
        auth_user: AuthenticatedUser = request.scope["user"]

        user = await _get_user_or_404(service, user_id, operation="delete_user")

        if user.id == auth_user.user_id:
            msg = "Cannot delete your own account"
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg)

        if user.role == HumanRole.SYSTEM:
            msg = "Cannot delete the system user"
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg)

        if user.role == HumanRole.CEO:
            msg = "Cannot delete the CEO user"
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg)

        try:
            deleted = await service.delete(
                NotBlankStr(user_id),
                deleted_by_user_id=NotBlankStr(auth_user.user_id),
            )
        except ConstraintViolationError as exc:
            if exc.constraint == LAST_OWNER_TRIGGER:
                msg = "Cannot delete the last owner"
            elif exc.constraint == LAST_CEO_TRIGGER:
                msg = "Cannot delete the last CEO"
            else:
                logger.error(
                    API_USER_SAVE_FAILED,
                    user_id=user_id,
                    intent="delete_user",
                    constraint=exc.constraint,
                )
                raise
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg) from exc
        except QueryError:
            logger.error(
                API_USER_SAVE_FAILED,
                user_id=user_id,
                intent="delete_user",
            )
            raise
        if not deleted:
            msg = f"User not found: {user_id}"
            logger.warning(API_RESOURCE_NOT_FOUND, reason=msg)
            raise NotFoundError(msg)

    # -- Org role grant/revoke -------------------------------------------

    @post(
        "/{user_id:str}/org-roles",
        status_code=201,
        guards=[
            per_op_rate_limit_from_policy("users.grant_org_role", key="user"),
        ],
    )
    async def grant_org_role(
        self,
        state: State,
        user_id: PathId,
        data: GrantOrgRoleRequest,
    ) -> ApiResponse[UserResponse]:
        """Grant an org-level role to a user.

        Args:
            state: Application state.
            user_id: Target user identifier.
            data: Role grant payload.

        Returns:
            Updated user response (HTTP 201).

        Raises:
            NotFoundError: If the user is not found.
            ConflictError: If the user already has the role.
            ValidationError: If department_admin without departments.
        """
        service = _service(state)
        user = await _get_user_or_404(service, user_id, operation="grant_org_role")

        if user.role == HumanRole.SYSTEM:
            msg = "Cannot assign org roles to the system user"
            logger.warning(API_VALIDATION_FAILED, reason=msg)
            raise ValidationError(msg)

        existing_roles = set(user.org_roles)
        if data.role in existing_roles:
            msg = f"User already has role: {data.role.value}"
            logger.warning(API_RESOURCE_CONFLICT, reason=msg)
            raise ConflictError(msg)

        if data.role == OrgRole.DEPARTMENT_ADMIN and not data.scoped_departments:
            msg = "department_admin role requires scoped_departments"
            logger.warning(API_VALIDATION_FAILED, reason=msg)
            raise ValidationError(msg)
        if data.role != OrgRole.DEPARTMENT_ADMIN and data.scoped_departments:
            msg = "scoped_departments can only be set for department_admin"
            logger.warning(API_VALIDATION_FAILED, reason=msg)
            raise ValidationError(msg)

        new_roles = (*user.org_roles, data.role)
        new_scoped = (
            tuple(
                sorted(
                    dedupe_preserving_order(
                        [*user.scoped_departments, *data.scoped_departments],
                    ),
                )
            )
            if data.role == OrgRole.DEPARTMENT_ADMIN
            else user.scoped_departments
        )
        now = datetime.now(UTC)
        updated = user.model_copy(
            update={
                "org_roles": new_roles,
                "scoped_departments": new_scoped,
                "updated_at": now,
            },
        )
        try:
            await service.save_update(
                updated,
                intent="grant_org_role",
                granted_org_role=data.role.value,
            )
        except ConstraintViolationError as exc:
            if exc.constraint == LAST_OWNER_TRIGGER:
                msg = "Cannot modify the last owner"
                logger.warning(API_RESOURCE_CONFLICT, reason=msg)
                raise ConflictError(msg) from exc
            logger.error(
                API_USER_SAVE_FAILED,
                user_id=user.id,
                intent="grant_org_role",
                role=data.role.value,
                constraint=exc.constraint,
            )
            raise
        except QueryError:
            logger.error(
                API_USER_SAVE_FAILED,
                user_id=user.id,
                intent="grant_org_role",
                role=data.role.value,
            )
            raise
        logger.info(
            SECURITY_PERMISSION_GRANTED,
            user_id=user.id,
            role=data.role.value,
            scoped_departments=tuple(data.scoped_departments),
        )
        return ApiResponse(data=_to_response(updated))

    @delete(
        "/{user_id:str}/org-roles/{role:str}",
        status_code=HTTP_204_NO_CONTENT,
        guards=[
            per_op_rate_limit_from_policy("users.revoke_org_role", key="user"),
        ],
    )
    async def revoke_org_role(
        self,
        state: State,
        user_id: PathId,
        role: str,
    ) -> None:
        """Revoke an org-level role from a user.

        Args:
            state: Application state.
            user_id: Target user identifier.
            role: OrgRole value to revoke.

        Raises:
            NotFoundError: If the user is not found.
            ValidationError: If the role value is invalid.
            ConflictError: If revoking the last owner.
        """
        service = _service(state)
        try:
            org_role = OrgRole(role)
        except ValueError:
            msg = f"Invalid org role: {role}"
            logger.warning(API_VALIDATION_FAILED, reason=msg)
            raise ValidationError(msg) from None

        user = await _get_user_or_404(service, user_id, operation="revoke_org_role")

        if org_role not in user.org_roles:
            msg = f"User does not have role: {role}"
            logger.warning(API_RESOURCE_NOT_FOUND, reason=msg)
            raise NotFoundError(msg)

        new_roles = tuple(r for r in user.org_roles if r != org_role)
        now = datetime.now(UTC)
        updated = user.model_copy(
            update={
                "org_roles": new_roles,
                "scoped_departments": ()
                if org_role == OrgRole.DEPARTMENT_ADMIN
                else user.scoped_departments,
                "updated_at": now,
            },
        )
        try:
            await service.save_update(
                updated,
                intent="revoke_org_role",
                revoked_org_role=role,
            )
        except ConstraintViolationError as exc:
            if exc.constraint == LAST_OWNER_TRIGGER:
                msg = "Cannot revoke the last owner role"
                logger.warning(API_RESOURCE_CONFLICT, reason=msg)
                raise ConflictError(msg) from exc
            logger.error(
                API_USER_SAVE_FAILED,
                user_id=user.id,
                intent="revoke_org_role",
                role=role,
                constraint=exc.constraint,
            )
            raise
        except QueryError:
            logger.error(
                API_USER_SAVE_FAILED,
                user_id=user.id,
                intent="revoke_org_role",
                role=role,
            )
            raise
        logger.info(
            SECURITY_PERMISSION_REVOKED,
            user_id=user.id,
            role=role,
        )
