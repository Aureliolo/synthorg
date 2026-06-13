# module-kind: controller
"""Active-embedder configuration endpoint (CEO / SYSTEM only)."""

from litestar import Controller, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_roles
from synthorg.api.state import AppState
from synthorg.core.auth.roles import HumanRole
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


class ActiveEmbedderResponse(BaseModel):
    """Active embedder configuration read from settings."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr | None = Field(
        default=None,
        description="Embedding provider name",
    )
    model: NotBlankStr | None = Field(
        default=None,
        description="Embedding model identifier",
    )
    dims: int | None = Field(
        default=None,
        ge=1,
        description="Embedding vector dimensions",
    )


class MemoryEmbedderController(Controller):
    """Read the active embedder configuration."""

    path = "/admin/memory"
    tags = ("admin", "memory")
    guards = [require_roles(HumanRole.CEO, HumanRole.SYSTEM)]  # noqa: RUF012

    @get("/embedder")
    async def get_active_embedder(
        self,
        state: State,
    ) -> ApiResponse[ActiveEmbedderResponse]:
        """Get the active embedder configuration.

        API-only: the active embedder is read by operational tooling and
        diagnostics; it has no dedicated dashboard surface.

        Returns:
            ``ApiResponse[ActiveEmbedderResponse]`` instance.

        Raises:
            Exception: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        result = ActiveEmbedderResponse()
        if app_state.slice(SettingsStateSlice).settings_service is not None:
            svc = require_service(
                app_state.slice(SettingsStateSlice).settings_service,
                "Settings Service",
            )
            try:
                # Each setting is independently optional: a successful
                # auto-selection persists only ``embedder_model`` +
                # ``embedder_dims``, so a missing ``embedder_provider``
                # is "unset", not a backend failure. Treat a per-field
                # SettingNotFoundError as ``None`` and reserve the outer
                # re-raise for genuine settings-backend errors.
                try:
                    provider_sv = await svc.get("memory", "embedder_provider")
                    provider_value = provider_sv.value or None
                except SettingNotFoundError:
                    provider_value = None
                try:
                    model_sv = await svc.get("memory", "embedder_model")
                    model_value = model_sv.value or None
                except SettingNotFoundError:
                    model_value = None
                try:
                    dims_sv = await svc.get("memory", "embedder_dims")
                    dims_raw = dims_sv.value
                except SettingNotFoundError:
                    dims_raw = None
                dims_value: int | None = None
                if dims_raw:
                    try:
                        parsed_dims = int(dims_raw)
                    except ValueError, TypeError:
                        logger.warning(
                            MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
                            setting="embedder_dims",
                            value=dims_raw,
                            reason="invalid integer value",
                        )
                    else:
                        # A non-positive embedding dimension ("0" / "-1")
                        # parses cleanly but is meaningless and would make
                        # ``ActiveEmbedderResponse`` validation 500 the read
                        # endpoint; treat corrupt stored dims as unset (None),
                        # same as unparseable input.
                        if parsed_dims >= 1:
                            dims_value = parsed_dims
                        else:
                            logger.warning(
                                MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
                                setting="embedder_dims",
                                value=dims_raw,
                                reason="value must be >= 1",
                            )
                result = ActiveEmbedderResponse(
                    provider=provider_value,
                    model=model_value,
                    dims=dims_value,
                )
            except Exception as exc:
                reraise_critical(exc)
                # Re-raise after logging instead of silently
                # swallowing -- a settings-service failure here would
                # otherwise look like "no embedder configured" to the
                # caller, masking the broken backend.
                logger.warning(
                    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
        return ApiResponse(data=result)
