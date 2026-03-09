"""Provider controller — read-only access to provider configs."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse
from ai_company.api.errors import NotFoundError
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.config.schema import (
    ProviderConfig,  # noqa: TC001
    ProviderModelConfig,  # noqa: TC001
)


class ProviderController(Controller):
    """Read-only access to LLM provider configurations."""

    path = "/providers"
    tags = ("providers",)

    @get()
    async def list_providers(
        self,
        state: State,
    ) -> ApiResponse[dict[str, ProviderConfig]]:
        """List all configured providers.

        Args:
            state: Application state.

        Returns:
            Provider configurations envelope.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=app_state.config.providers)

    @get("/{name:str}")
    async def get_provider(
        self,
        state: State,
        name: str,
    ) -> ApiResponse[ProviderConfig]:
        """Get a provider by name.

        Args:
            state: Application state.
            name: Provider name.

        Returns:
            Provider configuration envelope.

        Raises:
            NotFoundError: If the provider is not found.
        """
        app_state: AppState = state.app_state
        provider = app_state.config.providers.get(name)
        if provider is None:
            msg = f"Provider {name!r} not found"
            raise NotFoundError(msg)
        return ApiResponse(data=provider)

    @get("/{name:str}/models")
    async def list_models(
        self,
        state: State,
        name: str,
    ) -> ApiResponse[tuple[ProviderModelConfig, ...]]:
        """List models for a provider.

        Args:
            state: Application state.
            name: Provider name.

        Returns:
            Provider models envelope.

        Raises:
            NotFoundError: If the provider is not found.
        """
        app_state: AppState = state.app_state
        provider = app_state.config.providers.get(name)
        if provider is None:
            msg = f"Provider {name!r} not found"
            raise NotFoundError(msg)
        return ApiResponse(data=provider.models)
