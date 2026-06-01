"""Personality preset controller -- discovery and CRUD endpoints."""

from typing import Any, Final

from litestar import Controller, delete, get, post, put
from litestar.datastructures import State

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_personalities import (
    CreatePresetRequest,
    PresetDetailResponse,
    PresetSource,
    PresetSummaryResponse,
    UpdatePresetRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.observability import get_logger
from synthorg.persistence.state import persistence_of
from synthorg.templates.preset_service import (
    PersonalityPresetService,
    PresetEntry,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _to_summary(entry: PresetEntry) -> PresetSummaryResponse:
    """Convert a PresetEntry to a list summary response.

    Returns:
        ``PresetSummaryResponse`` instance.
    """
    raw_traits = entry.config.get("traits", ())
    traits = (
        tuple(str(t) for t in raw_traits)
        if isinstance(raw_traits, (list, tuple))
        else ()
    )
    return PresetSummaryResponse(
        name=entry.name,
        description=entry.description,
        traits=traits,
        source=PresetSource(entry.source),
    )


def _to_detail(entry: PresetEntry) -> PresetDetailResponse:
    """Convert a PresetEntry to a full detail response.

    Returns:
        ``PresetDetailResponse`` instance.
    """
    payload: dict[str, object] = {
        key: value for key, value in entry.config.items() if key != "description"
    }
    payload["name"] = entry.name
    payload["source"] = PresetSource(entry.source)
    payload["description"] = entry.description
    payload["created_at"] = entry.created_at
    payload["updated_at"] = entry.updated_at
    return PresetDetailResponse.model_validate(payload)


def _get_service(state: State) -> PersonalityPresetService:
    """Construct a PersonalityPresetService from app state.

    Returns:
        ``PersonalityPresetService`` instance.
    """
    repo = persistence_of(state.app_state).custom_presets
    return PersonalityPresetService(repository=repo)


class PersonalityPresetController(Controller):
    """Discovery and CRUD endpoints for personality presets."""

    path = "/personalities"
    tags = ("personalities",)

    @get(
        "/presets",
        guards=[require_read_access],
    )
    async def list_presets(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[PresetSummaryResponse]:
        """List all personality presets (builtin + custom).

        Returns:
            ``PaginatedResponse[PresetSummaryResponse]`` instance.
        """
        service = _get_service(state)
        entries = await service.list_all()
        summaries = tuple(_to_summary(e) for e in entries)
        page, meta = paginate_cursor(
            summaries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[PresetSummaryResponse](data=page, pagination=meta)

    @get(
        "/presets/{name:str}",
        guards=[require_read_access],
    )
    async def get_preset(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[PresetDetailResponse]:
        """Get full details of a personality preset.

        Returns:
            ``ApiResponse[PresetDetailResponse]`` instance.
        """
        service = _get_service(state)
        entry = await service.get(name)
        return ApiResponse[PresetDetailResponse](data=_to_detail(entry))

    @get(
        "/schema",
        guards=[require_read_access],
    )
    async def get_schema(self) -> ApiResponse[dict[str, Any]]:
        """Return the PersonalityConfig JSON schema.

        Returns:
            ``ApiResponse[dict[str, Any]]`` instance.
        """
        schema = PersonalityPresetService.get_schema()
        return ApiResponse[dict[str, Any]](data=schema)

    @post(
        "/presets",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("personalities.create", key="user"),
        ],
        status_code=201,
    )
    async def create_preset(
        self,
        state: State,
        data: CreatePresetRequest,
    ) -> ApiResponse[PresetDetailResponse]:
        """Create a custom personality preset.

        Returns:
            ``ApiResponse[PresetDetailResponse]`` instance.
        """
        service = _get_service(state)
        entry = await service.create(data.name, data.to_config_dict())
        return ApiResponse[PresetDetailResponse](data=_to_detail(entry))

    @put(
        "/presets/{name:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("personalities.update", key="user"),
        ],
    )
    async def update_preset(
        self,
        state: State,
        name: PathName,
        data: UpdatePresetRequest,
    ) -> ApiResponse[PresetDetailResponse]:
        """Update an existing custom personality preset.

        Returns:
            ``ApiResponse[PresetDetailResponse]`` instance.
        """
        service = _get_service(state)
        entry = await service.update(name, data.to_config_dict())
        return ApiResponse[PresetDetailResponse](data=_to_detail(entry))

    @delete(
        "/presets/{name:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("personalities.delete", key="user"),
        ],
        status_code=200,
    )
    async def delete_preset(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[None]:
        """Delete a custom personality preset.

        Returns:
            ``ApiResponse[None]`` instance.
        """
        service = _get_service(state)
        await service.delete(name)
        return ApiResponse[None](data=None)
