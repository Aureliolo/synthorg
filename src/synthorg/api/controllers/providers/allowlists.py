# module-kind: controller
"""Provider discovery SSRF-allowlist endpoints."""

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg.api.dto import ApiResponse
from synthorg.api.dto_discovery import (
    AddAllowlistEntryRequest,
    DiscoveryPolicyResponse,
    RemoveAllowlistEntryRequest,
)
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.providers.state import provider_management_of


class ProviderAllowlistsController(Controller):
    """Read and mutate the provider discovery SSRF allowlist."""

    path = "/providers"
    tags = ("providers",)

    @get(
        "/discovery-policy",
        guards=[require_read_access],
    )
    async def get_discovery_policy(
        self,
        state: State,
    ) -> ApiResponse[DiscoveryPolicyResponse]:
        """Return the current provider discovery SSRF allowlist.

        Args:
            state: Application state.

        Returns:
            Current discovery policy envelope.
        """
        app_state: AppState = state.app_state
        policy = await provider_management_of(app_state).get_discovery_policy()
        return ApiResponse(
            data=DiscoveryPolicyResponse.model_validate(
                policy,
                from_attributes=True,
            ),
        )

    @post(
        "/discovery-policy/entries",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.allowlist_add",
                key="user",
            ),
        ],
    )
    async def add_allowlist_entry(
        self,
        state: State,
        data: AddAllowlistEntryRequest,
    ) -> ApiResponse[DiscoveryPolicyResponse]:
        """Add a custom host:port entry to the discovery allowlist.

        Args:
            state: Application state.
            data: Request with the host:port entry to add.

        Returns:
            Updated discovery policy envelope.
        """
        app_state: AppState = state.app_state
        policy = await provider_management_of(app_state).add_custom_allowlist_entry(
            data.host_port,
        )
        return ApiResponse(
            data=DiscoveryPolicyResponse.model_validate(
                policy,
                from_attributes=True,
            ),
        )

    @post(
        "/discovery-policy/remove-entry",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.allowlist_remove",
                key="user",
            ),
        ],
    )
    async def remove_allowlist_entry(
        self,
        state: State,
        data: RemoveAllowlistEntryRequest,
    ) -> ApiResponse[DiscoveryPolicyResponse]:
        """Remove a host:port entry from the discovery allowlist.

        Args:
            state: Application state.
            data: Request with the host:port entry to remove.

        Returns:
            Updated discovery policy envelope.
        """
        app_state: AppState = state.app_state
        policy = await provider_management_of(app_state).remove_custom_allowlist_entry(
            data.host_port,
        )
        return ApiResponse(
            data=DiscoveryPolicyResponse.model_validate(
                policy,
                from_attributes=True,
            ),
        )
