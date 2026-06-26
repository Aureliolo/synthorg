# module-kind: controller
"""Provider credential rotation and rate-limit override endpoints."""

from litestar import Controller, get, patch, post
from litestar.datastructures import State

from synthorg.api.dto import ApiResponse
from synthorg.api.dto_provider_capabilities import (
    CredentialsRotateRequest,
    RateLimitsResponse,
    RateLimitsUpdateRequest,
)
from synthorg.api.dto_providers import ProviderResponse, to_provider_response
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.providers.state import provider_management_of


class ProviderCapabilitiesController(Controller):
    """Credential rotation and rate-limit overrides for a provider."""

    path = "/providers"
    tags = ("providers",)

    @post(
        "/{name:str}/credentials/rotate",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.rotate_credentials",
                key="user",
            ),
        ],
    )
    async def rotate_credentials(
        self,
        state: State,
        name: PathName,
        data: CredentialsRotateRequest,
    ) -> ApiResponse[ProviderResponse]:
        """Rotate the secret credentials on an existing provider.

        Args:
            state: Application state.
            name: Provider name.
            data: Discriminated-union rotation payload keyed by
                ``auth_type``.

        Returns:
            Updated provider response (secrets stripped).

        Raises:
            ProviderNotFoundError: If the provider does not exist (404,
                mapped by the domain handler from class metadata).
            ProviderValidationError: If the rotation payload's ``auth_type``
                does not match the provider's persisted ``auth_type`` (422).
        """
        app_state: AppState = state.app_state
        updated = await provider_management_of(app_state).rotate_credentials(name, data)
        return ApiResponse(data=to_provider_response(updated, name=None))

    @get(
        "/{name:str}/rate-limits",
        guards=[require_read_access],
    )
    async def get_rate_limits(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[RateLimitsResponse]:
        """Read the persisted rate-limit configuration for one provider.

        Args:
            state: Application state.
            name: Provider name.

        Returns:
            ``RateLimitsResponse`` with ``0`` meaning unlimited.

        Raises:
            ProviderNotFoundError: If the provider does not exist (404,
                mapped by the domain handler from class metadata).
        """
        app_state: AppState = state.app_state
        data = await provider_management_of(app_state).get_rate_limits(name)
        return ApiResponse(data=data)

    @patch(
        "/{name:str}/rate-limits",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.update_rate_limits",
                key="user",
            ),
        ],
    )
    async def update_rate_limits(
        self,
        state: State,
        name: PathName,
        data: RateLimitsUpdateRequest,
    ) -> ApiResponse[RateLimitsResponse]:
        """Apply a partial update to the provider's rate-limit config.

        Args:
            state: Application state.
            name: Provider name.
            data: Partial-update payload; at least one field required.

        Returns:
            ``RateLimitsResponse`` reflecting the new effective config.

        Raises:
            ProviderNotFoundError: If the provider does not exist (404,
                mapped by the domain handler from class metadata).
            ProviderValidationError: If the merged config fails validation (422).
        """
        app_state: AppState = state.app_state
        updated = await provider_management_of(app_state).update_rate_limits(name, data)
        return ApiResponse(data=updated)
