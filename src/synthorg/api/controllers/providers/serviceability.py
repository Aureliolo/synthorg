# module-kind: controller
"""Reading whether a model serves work, as opposed to whether it answers.

Sibling of the health controller and deliberately not folded into it: that
one answers "is this connection reachable", over a day, per provider. This
one answers "is this pair usable right now", over a recent window, per
``(provider, model)``. They disagree exactly when it matters, and a surface
that returned one number would have to pick which question to drop.
"""

from litestar import Controller, get
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.controllers._provider_helpers import require_provider
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import PathName
from synthorg.api.state import AppState
from synthorg.providers.serviceability import ModelServiceability
from synthorg.providers.serviceability_settings import (
    resolve_serviceability_thresholds,
)
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import SettingsStateSlice


class ProviderServiceabilityController(Controller):
    """Per-``(provider, model)`` serviceability reads."""

    path = "/providers"
    tags = ("providers",)

    @get(
        "/serviceability",
        guards=[require_read_access],
    )
    async def list_serviceability(
        self,
        state: State,
    ) -> ApiResponse[list[ModelServiceability]]:
        """Report every pair a real call has exercised recently.

        Returns:
            ``ApiResponse`` carrying one view per ``(provider, model)``.
        """
        app_state: AppState = state.app_state
        tracker = require_service(
            app_state.slice(ProvidersStateSlice).health_tracker,
            "Provider Health Tracker",
        )
        thresholds = await resolve_serviceability_thresholds(
            app_state.slice(SettingsStateSlice).config_resolver
        )
        views = await tracker.get_all_serviceability(thresholds=thresholds)
        return ApiResponse(data=list(views.values()))

    @get(
        "/{name:str}/serviceability",
        guards=[require_read_access],
    )
    async def get_provider_serviceability(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[list[ModelServiceability]]:
        """Report each model this provider has recently served.

        Scoped by provider rather than returning a single roll-up, because
        one connection routinely serves a healthy model and a failing one at
        the same time, and a connection-level number is the average that
        hides it.

        Raises:
            NotFoundError: If *name* is not a configured provider. An empty
                list is a true statement about a provider that has served
                nothing; returning it for a name that does not exist would
                tell an operator who mistyped one that all is well.

        Returns:
            ``ApiResponse`` carrying one view per model on *name*.
        """
        app_state: AppState = state.app_state
        await require_provider(app_state, name)
        tracker = require_service(
            app_state.slice(ProvidersStateSlice).health_tracker,
            "Provider Health Tracker",
        )
        thresholds = await resolve_serviceability_thresholds(
            app_state.slice(SettingsStateSlice).config_resolver
        )
        views = await tracker.get_all_serviceability(thresholds=thresholds)
        return ApiResponse(
            data=[view for (provider, _), view in views.items() if provider == name]
        )
