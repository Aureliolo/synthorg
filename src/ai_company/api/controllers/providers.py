"""Provider controller — read-only access to provider configs."""

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.dto import ApiResponse
from ai_company.api.errors import NotFoundError
from ai_company.api.guards import require_read_access
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.config.schema import (
    ProviderConfig,  # noqa: TC001
    ProviderModelConfig,  # noqa: TC001
)
from ai_company.observability import get_logger
from ai_company.observability.events.api import API_RESOURCE_NOT_FOUND

logger = get_logger(__name__)


def _safe_provider(provider: ProviderConfig) -> ProviderConfig:
    """Return a copy of the provider config with api_key stripped."""
    return provider.model_copy(update={"api_key": None})


class ProviderController(Controller):
    """Read-only access to LLM provider configurations."""

    path = "/providers"
    tags = ("providers",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_providers(
        self,
        state: State,
    ) -> ApiResponse[dict[str, ProviderConfig]]:
        """List all configured providers.

        Args:
            state: Application state.

        Returns:
            Provider configurations envelope (api_key stripped).
        """
        app_state: AppState = state.app_state
        safe = {
            name: _safe_provider(p) for name, p in app_state.config.providers.items()
        }
        return ApiResponse(data=safe)

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
            Provider configuration envelope (api_key stripped).

        Raises:
            NotFoundError: If the provider is not found.
        """
        app_state: AppState = state.app_state
        provider = app_state.config.providers.get(name)
        if provider is None:
            msg = f"Provider {name!r} not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="provider", name=name)
            raise NotFoundError(msg)
        return ApiResponse(data=_safe_provider(provider))

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
            logger.warning(API_RESOURCE_NOT_FOUND, resource="provider", name=name)
            raise NotFoundError(msg)
        return ApiResponse(data=provider.models)
