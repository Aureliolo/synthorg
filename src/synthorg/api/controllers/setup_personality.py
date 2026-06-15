"""Personality-related setup controller endpoints.

Extracted from ``setup.py`` to keep controllers under the 800-line limit.
Handles agent personality preset assignment and preset listing.
"""

import json
from typing import Annotated

from litestar import Controller, get, put
from litestar.datastructures import State
from litestar.params import PathParameter
from litestar.status_codes import HTTP_200_OK

from synthorg.api.controllers.setup._runtime_wiring import (
    AGENT_LOCK as _AGENT_LOCK,
)
from synthorg.api.controllers.setup._runtime_wiring import (
    COMPLETE_LOCK as _COMPLETE_LOCK,
)
from synthorg.api.controllers.setup._status_checks import (
    validate_agent_index as _validate_agent_index,
)
from synthorg.api.controllers.setup.company_helpers import (
    check_setup_not_complete as _check_setup_not_complete,
)
from synthorg.api.controllers.setup_agents import (
    agent_dict_to_summary,
    get_existing_agents,
)
from synthorg.api.controllers.setup_models import (
    PersonalityPresetInfoResponse,
    SetupAgentSummary,
    UpdateAgentPersonalityRequest,
)
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_ceo, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_AGENT_PERSONALITY_UPDATED,
    SETUP_PERSONALITY_PRESETS_LISTED,
    SETUP_PRESET_NOT_FOUND,
)
from synthorg.persistence.state import persistence_of
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)


class SetupPersonalityController(Controller):
    """Setup wizard endpoints for personality presets."""

    path = "/setup"
    tags = ("setup",)

    @put(
        "/agents/{agent_index:int}/personality",
        status_code=HTTP_200_OK,
        guards=[require_ceo],
    )
    async def update_agent_personality(
        self,
        agent_index: Annotated[int, PathParameter(ge=0)],
        data: UpdateAgentPersonalityRequest,
        state: State,
    ) -> ApiResponse[SetupAgentSummary]:
        """Update a single agent's personality preset during setup.

        Args:
            agent_index: Zero-based index of the agent to update.
            data: New personality preset assignment.
            state: Application state.

        Returns:
            Updated agent summary.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the agent index is out of range.
            ValidationError: If the requested personality preset name
                is not a known builtin or custom preset.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        from synthorg.templates.preset_service import (  # noqa: PLC0415
            fetch_custom_presets_map,
        )
        from synthorg.templates.presets import (  # noqa: PLC0415
            get_personality_preset,
        )

        custom_presets = await fetch_custom_presets_map(
            persistence_of(app_state).custom_presets,
        )
        try:
            personality_dict = get_personality_preset(
                data.personality_preset,
                custom_presets=custom_presets,
            )
        except KeyError:
            from synthorg.core.domain_errors import ValidationError  # noqa: PLC0415

            logger.warning(
                SETUP_PRESET_NOT_FOUND,
                preset=data.personality_preset,
                agent_index=agent_index,
            )
            msg = f"Unknown personality preset {data.personality_preset!r}"
            raise ValidationError(msg) from None

        # Hold both locks and re-check setup-not-complete INSIDE them so a
        # concurrent ``/setup/complete`` cannot slip between the check and the
        # ``company.agents`` read-modify-write (matches ``SetupAgentsController``).
        async with _COMPLETE_LOCK, _AGENT_LOCK:
            await _check_setup_not_complete(settings_svc)
            agents = await get_existing_agents(settings_svc)
            _validate_agent_index(agent_index, agents)
            updated_agent = {
                **agents[agent_index],
                "personality_preset": data.personality_preset,
                "personality": personality_dict,
            }
            agents = [
                *agents[:agent_index],
                updated_agent,
                *agents[agent_index + 1 :],
            ]
            await settings_svc.set(
                "company",
                "agents",
                json.dumps(agents),
            )

        logger.info(
            SETUP_AGENT_PERSONALITY_UPDATED,
            agent_index=agent_index,
            personality_preset=data.personality_preset,
        )

        return ApiResponse(
            data=agent_dict_to_summary(agents[agent_index]),
        )

    @get(
        "/personality-presets",
        guards=[require_read_access],
    )
    async def list_personality_presets(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[PersonalityPresetInfoResponse]:
        """List all available personality presets, paginated by name.

        Args:
            state: Application state.
            cursor: Opaque cursor from a previous page.
            limit: Page size.

        Returns:
            Paginated personality presets.
        """
        from synthorg.templates.presets import (  # noqa: PLC0415
            PERSONALITY_PRESETS,
        )

        app_state: AppState = state.app_state
        presets = tuple(
            PersonalityPresetInfoResponse(
                name=name,
                description=str(preset["description"]),
            )
            for name, preset in sorted(PERSONALITY_PRESETS.items())
        )

        page, meta = paginate_cursor(
            presets,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        logger.debug(
            SETUP_PERSONALITY_PRESETS_LISTED,
            count=len(page),
        )
        return PaginatedResponse[PersonalityPresetInfoResponse](
            data=page,
            pagination=meta,
        )
