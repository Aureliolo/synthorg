# module-kind: controller
"""User account controller -- CEO-only CRUD for human users."""

import uuid
from datetime import UTC, datetime
from typing import Final

from litestar import Controller, Request, delete, get, patch, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.controllers.users._shared import (
    UserResponse,
    _get_user_or_404,
    _service,
    _to_response,
)
from synthorg.api.cursor import decode_keyset_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_ceo
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_keyset_meta,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import AuthenticatedUser, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
    API_USER_SAVE_FAILED,
    API_VALIDATION_FAILED,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

# Derive from AuthConfig default to prevent silent divergence.
_MIN_PASSWORD_LENGTH: int = AuthConfig.model_fields["min_password_length"].default

# Roles that cannot be assigned via the user management API.
_FORBIDDEN_ROLES: frozenset[HumanRole] = frozenset({HumanRole.SYSTEM})


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


def _validate_assignable_role(role: HumanRole) -> None:
    """Reject roles that cannot be assigned via the API.

    Raises:
        ValidationError: Raised on the corresponding failure path.
    """
    if role in _FORBIDDEN_ROLES:
        msg = f"Cannot assign role: {role.value}"
        logger.warning(API_VALIDATION_FAILED, reason=msg)
        raise ValidationError(msg)


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
            QueryError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state

        _validate_assignable_role(data.role)

        if len(data.password) < _MIN_PASSWORD_LENGTH:
            msg = f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"
            logger.warning(API_VALIDATION_FAILED, reason=msg)
            raise ValidationError(msg)

        now = datetime.now(UTC)
        auth_service = require_service(
            app_state.slice(ApiCoreStateSlice).auth_service, "Auth Service"
        )
        password_hash = await auth_service.hash_password(
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
            decode_keyset_cursor(cursor, secret=cursor_secret_of(app_state))
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
            secret=cursor_secret_of(app_state),
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
        request: Request[object, object, State],
        user_id: PathId,
        data: UpdateUserRoleRequest,
    ) -> ApiResponse[UserResponse]:
        """Update a user's role.

        Args:
            state: Application state.
            request: Incoming request, carrying the authenticated actor.
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
            QueryError: Raised on the corresponding failure path.
        """
        service = _service(state)
        auth_user: AuthenticatedUser = request.scope["user"]

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
                principal=str(auth_user.user_id),
                old_role=user.role.value,
                new_role=data.role.value,
            )
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
        request: Request[object, object, State],
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
            QueryError: Raised on the corresponding failure path.
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
