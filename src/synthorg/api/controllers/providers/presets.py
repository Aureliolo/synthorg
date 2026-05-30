# module-kind: controller
"""Provider preset operator-override endpoints."""

from litestar import Controller, delete, get, patch
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg._core.features import require_service
from synthorg.api.controllers._workflow_helpers import audit_actor_from_context
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_provider_capabilities import (
    PresetOverride,
    PresetOverrideUpdateRequest,
)
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
from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.presets import get_preset
from synthorg.providers.state import ProvidersStateSlice

logger = get_logger(__name__)


class ProviderPresetsController(Controller):
    """Read, upsert, and delete operator overrides on in-code presets."""

    path = "/providers"
    tags = ("providers",)

    @get(
        "/presets/{preset_name:str}/override",
        guards=[require_read_access],
    )
    async def get_preset_override(
        self,
        state: State,
        preset_name: PathName,
    ) -> ApiResponse[PresetOverride | None]:
        """Read the operator override for ``preset_name`` (or null).

        Args:
            state: Application state.
            preset_name: Preset whose override to read.  Must match an
                in-code preset; unknown names return 404.

        Returns:
            ``PresetOverride`` if one is persisted, otherwise ``None``.

        Raises:
            NotFoundError: If the preset name is unknown.
        """
        if get_preset(preset_name) is None:
            msg = f"Unknown preset {preset_name!r}"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="preset",
                name=preset_name,
            )
            raise NotFoundError(msg)

        app_state: AppState = state.app_state
        preset_service = require_service(
            app_state.slice(ProvidersStateSlice).preset_override_service,
            "Preset Override Service",
        )
        override = await preset_service.get_override(preset_name)
        return ApiResponse(data=override)

    @patch(
        "/presets/{preset_name:str}/override",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.update_preset_override",
                key="user",
            ),
        ],
    )
    async def update_preset_override(
        self,
        state: State,
        preset_name: PathName,
        data: PresetOverrideUpdateRequest,
    ) -> ApiResponse[PresetOverride]:
        """Apply a partial override on top of ``preset_name``.

        Args:
            state: Application state.
            preset_name: Preset whose override to write.
            data: Partial override payload.

        Returns:
            The persisted override.

        Raises:
            NotFoundError: If the preset name is unknown.
            ValidationError: If the override shape conflicts with
                the preset's kind (cloud vs local).
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        # Preflight existence check: classifying "preset not found"
        # vs "override invalid for this preset's kind" via substring
        # match on the error message is brittle and silently misroutes
        # any unrelated validation error that happens to contain the
        # phrase "Unknown preset".  Fail-fast with 404 here when the
        # name is unknown; everything that survives this check is a
        # genuine validation failure on the override shape.
        if get_preset(preset_name) is None:
            msg = f"Unknown preset {preset_name!r}"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="preset",
                name=preset_name,
            )
            raise NotFoundError(msg)
        preset_service = require_service(
            app_state.slice(ProvidersStateSlice).preset_override_service,
            "Preset Override Service",
        )
        try:
            saved = await preset_service.upsert_override(
                preset_name,
                data,
                actor=actor,
            )
        except ProviderValidationError as exc:
            logger.warning(
                API_VALIDATION_FAILED,
                resource="preset",
                name=preset_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ValidationError(safe_error_description(exc)) from exc
        return ApiResponse(data=saved)

    @delete(
        "/presets/{preset_name:str}/override",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy(
                "providers.delete_preset_override",
                key="user",
            ),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_preset_override(
        self,
        state: State,
        preset_name: PathName,
    ) -> None:
        """Drop the override for ``preset_name``.

        Idempotent: returns 204 whether or not a row existed for a
        VALID preset name; an unknown preset name returns 404 to
        match the upsert path.

        Args:
            state: Application state.
            preset_name: Preset whose override to delete.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        actor = audit_actor_from_context()
        # Match the upsert path's preflight: an unknown preset name
        # is a 404, not a silent no-op.  Without this, callers can
        # DELETE arbitrary strings with no signal -- defeats the
        # accountability intent of the audit row.
        if get_preset(preset_name) is None:
            msg = f"Unknown preset {preset_name!r}"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="preset",
                name=preset_name,
            )
            raise NotFoundError(msg)
        preset_service = require_service(
            app_state.slice(ProvidersStateSlice).preset_override_service,
            "Preset Override Service",
        )
        await preset_service.delete_override(
            preset_name,
            actor=actor,
        )
