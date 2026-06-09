# module-kind: controller
"""User org-role controller -- CEO-only grant/revoke of org-level roles."""

from datetime import UTC, datetime

from litestar import Controller, delete, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import BaseModel, ConfigDict

from synthorg.api.controllers.users._shared import (
    UserResponse,
    _get_user_or_404,
    _service,
    _to_response,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo
from synthorg.api.path_params import PathId, PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.auth.models import OrgRole
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
from synthorg.persistence.constraint_tokens import LAST_OWNER_TRIGGER

logger = get_logger(__name__)


class GrantOrgRoleRequest(BaseModel):
    """Request body for granting an org-level role."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role: OrgRole
    scoped_departments: tuple[NotBlankStr, ...] = ()


class UserOrgRolesController(Controller):
    """CEO-only endpoints for granting and revoking org-level roles.

    All endpoints require the CEO role.
    """

    path = "/users"
    tags = ("users",)
    guards = [require_ceo]  # noqa: RUF012

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
            ConstraintViolationError: Raised on the corresponding failure path.
            QueryError: Raised on the corresponding failure path.
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
        role: PathName,
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
            ConstraintViolationError: Raised on the corresponding failure path.
            QueryError: Raised on the corresponding failure path.
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
