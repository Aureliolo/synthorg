# module-kind: controller
"""Active-embedder configuration endpoint (CEO / SYSTEM only)."""

from litestar import Controller, get, post
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_roles
from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.auth.roles import HumanRole
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.core.vector_limits import (
    HNSW_HALFVEC_MAX_DIMENSIONS,
    HNSW_VECTOR_MAX_DIMENSIONS,
    IndexSupport,
    index_support_for,
)
from synthorg.memory.embedding.probe import probe_embedder_dims
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.model_ref import ModelRef, parse_model_ref
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


class EmbedderProbeRequest(BaseModel):
    """The binding whose vector width should be measured."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Embedding provider name")
    model_id: NotBlankStr = Field(description="Embedding model identifier")


class EmbedderProbeResponse(BaseModel):
    """A model's measured width and what this store can do with it.

    Attributes:
        dims: The width the model actually emitted, measured by asking it.
        index_support: What the vector store can do at that width.
        vector_ceiling: Widest full-precision vector an index accepts.
        halfvec_ceiling: Widest half-precision vector an index accepts.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    dims: int = Field(ge=1, description="Measured embedding vector width")
    index_support: IndexSupport = Field(
        description="What the vector store can do at this width",
    )
    vector_ceiling: int = Field(
        ge=1,
        description="Widest full-precision vector an HNSW index accepts",
    )
    halfvec_ceiling: int = Field(
        ge=1,
        description="Widest half-precision vector an HNSW index accepts",
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
                # Both halves of the binding come out of one MODEL_REF
                # value, so they cannot disagree. Each setting is still
                # independently optional: treat a per-field
                # SettingNotFoundError as ``None`` and reserve the outer
                # re-raise for genuine settings-backend errors.
                try:
                    model_sv = await svc.get("memory", "embedder_model")
                    ref = parse_model_ref(model_sv.value or "")
                except SettingNotFoundError:
                    ref = ModelRef()
                provider_value = ref.provider or None
                model_value = ref.model_id or None
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

    @post("/embedder/probe")
    async def probe_embedder(
        self,
        state: State,
        data: EmbedderProbeRequest,
    ) -> ApiResponse[EmbedderProbeResponse]:
        """Measure a candidate embedder's width and report what it costs.

        Issues one real embedding call, deliberately and only when asked:
        the width is a property of the model and the model is the only
        authority on it, so there is nothing to read from a table. Scoped to
        the single binding the operator is considering rather than run
        across the catalogue, because on a metered provider every probe is
        billed and sweeping them all would spend the operator's quota to
        populate a dropdown.

        Nothing is chosen here. The response states the measured width and
        the mechanical consequence for this store; which embedder to run
        stays the operator's call.

        Args:
            state: Application state.
            data: The provider/model pair to measure.

        Returns:
            The measured width alongside the store's ceilings.

        Raises:
            MemoryEmbeddingError: When the model cannot be reached or
                answers with no vector, so its width is unknown.
        """
        app_state: AppState = state.app_state
        dims = await probe_embedder_dims(
            provider=data.provider,
            model=data.model_id,
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
        )
        return ApiResponse(
            data=EmbedderProbeResponse(
                dims=dims,
                index_support=index_support_for(dims),
                vector_ceiling=HNSW_VECTOR_MAX_DIMENSIONS,
                halfvec_ceiling=HNSW_HALFVEC_MAX_DIMENSIONS,
            )
        )
