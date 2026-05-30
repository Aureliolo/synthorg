# module-kind: controller
"""Provider credential rotation and rate-limit override endpoints."""

from litestar import Controller, get, patch, post
from litestar.datastructures import State

from synthorg.api.controllers._workflow_helpers import audit_actor_from_context
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
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_NOT_FOUND,
    API_VALIDATION_FAILED,
)
from synthorg.providers.errors import ProviderNotFoundError, ProviderValidationError
from synthorg.providers.state import provider_management_of

logger = get_logger(__name__)


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
            NotFoundError: If the provider does not exist.
            ValidationError: If the rotation payload's ``auth_type``
                does not match the provider's persisted ``auth_type``.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            updated = await provider_management_of(app_state).rotate_credentials(
                name,
                data,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
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
            NotFoundError: If the provider does not exist.
        """
        app_state: AppState = state.app_state
        try:
            data = await provider_management_of(app_state).get_rate_limits(name)
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
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
            NotFoundError: If the provider does not exist.
            ValidationError: If the merged config fails validation.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        try:
            updated = await provider_management_of(app_state).update_rate_limits(
                name,
                data,
                actor=actor,
            )
        except ProviderNotFoundError as exc:
            msg = f"Provider {name!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="provider",
                name=name,
            )
            raise NotFoundError(msg) from exc
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="provider",
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=updated)
