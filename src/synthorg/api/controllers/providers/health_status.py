# module-kind: controller
"""Reading provider health, and re-deriving it on demand.

Split from the CRUD controller because these answer a different question:
not what a provider is configured as, but whether it answers right now. The
read replays what was recorded; the rechecks go and find out.
"""

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg.api.controllers._provider_helpers import read_provider_health
from synthorg.api.controllers._provider_recheck import (
    recheck_all_provider_health,
    recheck_provider_health,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.config.provider_configs_read import (
    ProviderConfigDiagnostics,
    ProviderConfigsStatus,
)
from synthorg.providers.health import ProviderHealthSummary
from synthorg.providers.state import ProvidersStateSlice


class ProviderHealthController(Controller):
    """Provider health reads and on-demand rechecks."""

    path = "/providers"
    tags = ("providers",)

    @get(
        "/{name:str}/health",
        guards=[require_read_access],
    )
    async def get_provider_health(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[ProviderHealthSummary]:
        """Get provider health summary (enriched with cost data).

        Raises:
            NotFoundError: If the provider is not found.

        Returns:
            ``ApiResponse[ProviderHealthSummary]`` instance.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=await read_provider_health(app_state, name))

    @post(
        "/{name:str}/health/recheck",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("providers.test", key="user"),
        ],
    )
    async def recheck_provider_health(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[ProviderHealthSummary]:
        """Call *name* now and report the health that call produces.

        Raises:
            NotFoundError: If the provider is not found.
            ProviderTimeoutError: If the provider did not answer within
                ``api.health_recheck_timeout_seconds``; answers 504 and
                retryable, so a client knows to try again.

        Returns:
            ``ApiResponse[ProviderHealthSummary]`` reflecting the new call.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=await recheck_provider_health(app_state, name))

    @get(
        "/config-diagnostics",
        guards=[require_read_access],
    )
    async def get_provider_config_diagnostics(
        self,
        state: State,
    ) -> ApiResponse[ProviderConfigDiagnostics]:
        """Report what the last read of the persisted provider config made of it.

        Answers the one question an empty provider list cannot: whether
        this deployment has nothing configured, or has a configuration it
        could not read. Those look identical from every other endpoint and
        want opposite things from an operator.

        Returns:
            ``ApiResponse[ProviderConfigDiagnostics]``. A read that has not
            happened yet (persistence not connected, or an anonymous boot)
            reports ``OK`` with nothing rejected, which is what an
            unconfigured deployment reports too, because at that point the
            two genuinely are the same.
        """
        app_state: AppState = state.app_state
        diagnostics = app_state.slice(ProvidersStateSlice).config_diagnostics
        return ApiResponse(
            data=diagnostics
            or ProviderConfigDiagnostics(status=ProviderConfigsStatus.OK),
        )

    @post(
        "/health/recheck",
        guards=[
            require_ceo_or_manager,
            # Its own budget rather than the per-provider one: a single call
            # here issues a billed completion to every configured provider,
            # so charging it as one call would let provider count set the
            # spend an operator can trigger.
            per_op_rate_limit_from_policy("providers.health_recheck_all", key="user"),
        ],
    )
    async def recheck_all_provider_health(
        self,
        state: State,
    ) -> ApiResponse[dict[str, ProviderHealthSummary]]:
        """Call every configured provider now and report the results.

        Serves the one control an operator has when the dashboard says a
        provider is unhealthy but they believe they have fixed it, without
        making them open each provider in turn.

        Returns:
            ``ApiResponse`` mapping provider name to its new health summary.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=await recheck_all_provider_health(app_state))
