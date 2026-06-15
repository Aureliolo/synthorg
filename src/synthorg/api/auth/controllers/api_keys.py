# module-kind: controller
"""API-key management endpoints: issue, list, and revoke API keys."""

from litestar import Controller, Request, delete, get, post
from litestar.datastructures import State

from synthorg.api.api_core_state import api_key_service_of
from synthorg.api.auth.api_key_service import ApiKeyView, IssuedApiKey
from synthorg.api.auth.controller_dtos import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import UnauthorizedError
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_AUTH_FAILED

logger = get_logger(__name__)


def _require_auth(request: Request[object, object, State]) -> AuthenticatedUser:
    """Return the authenticated user or raise 401.

    The class guard already enforces a read role; this resolves the
    concrete identity for ownership / role-ceiling decisions.

    Returns:
        The authenticated user.

    Raises:
        UnauthorizedError: When no authenticated user is on the request.
    """
    auth_user = request.scope.get("user")
    if not isinstance(auth_user, AuthenticatedUser):
        logger.warning(SECURITY_AUTH_FAILED, reason="api_key_unauthenticated")
        msg = "Authentication required"
        raise UnauthorizedError(msg)
    return auth_user


def _to_response(view: ApiKeyView) -> ApiKeyResponse:
    """Map a service view to the wire response DTO.

    Returns:
        The hash-free response DTO.
    """
    return ApiKeyResponse(
        id=view.id,
        name=view.name,
        role=view.role,
        user_id=view.user_id,
        created_at=view.created_at,
        expires_at=view.expires_at,
        revoked=view.revoked,
    )


class AuthApiKeysController(Controller):
    """Issue, list, and revoke API keys for the authenticated user."""

    path = "/auth/api-keys"
    tags = ("auth",)
    guards = [require_read_access]  # noqa: RUF012

    @post(
        summary="Issue an API key",
        status_code=201,
        guards=[per_op_rate_limit_from_policy("auth.api_keys_issue", key="user")],
    )
    async def issue_key(
        self,
        state: State,
        data: CreateApiKeyRequest,
        request: Request[object, object, State],
    ) -> ApiResponse[CreatedApiKeyResponse]:
        """Mint a new API key owned by the caller (plaintext shown once).

        Returns:
            The created key metadata plus the one-time plaintext.

        Raises:
            UnauthorizedError: When the caller is not authenticated.
            ForbiddenError: When the requested role exceeds the caller's.
        """
        app_state: AppState = state.app_state
        auth_user = _require_auth(request)
        issued: IssuedApiKey = await api_key_service_of(app_state).issue(
            owner=auth_user,
            name=data.name,
            role=data.role,
            expires_at=data.expires_at,
        )
        return ApiResponse(
            data=CreatedApiKeyResponse(
                key=_to_response(issued.view),
                api_key=issued.plaintext,
            ),
        )

    @get(
        summary="List the caller's API keys",
        guards=[per_op_rate_limit_from_policy("auth.api_keys_list", key="user")],
    )
    async def list_keys(
        self,
        state: State,
        request: Request[object, object, State],
    ) -> ApiResponse[list[ApiKeyResponse]]:
        """List the caller's own API keys (never includes the hash).

        Returns:
            The caller's keys.

        Raises:
            UnauthorizedError: When the caller is not authenticated.
        """
        app_state: AppState = state.app_state
        auth_user = _require_auth(request)
        views = await api_key_service_of(app_state).list_for_user(auth_user.user_id)
        return ApiResponse(data=[_to_response(v) for v in views])

    @delete(
        "/{key_id:str}",
        status_code=204,
        summary="Revoke an API key",
        guards=[per_op_rate_limit_from_policy("auth.api_keys_revoke", key="user")],
    )
    async def revoke_key(
        self,
        state: State,
        key_id: PathId,
        request: Request[object, object, State],
    ) -> None:
        """Revoke one of the caller's API keys (CEO may revoke any).

        Raises:
            UnauthorizedError: When the caller is not authenticated.
            ApiKeyNotFoundError: When the key is missing or not owned by
                the caller (404, never 403, to prevent enumeration).
        """
        app_state: AppState = state.app_state
        auth_user = _require_auth(request)
        await api_key_service_of(app_state).revoke(
            key_id=key_id,
            requester=auth_user,
        )
