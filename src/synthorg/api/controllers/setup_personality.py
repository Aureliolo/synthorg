"""Personality-related setup controller endpoints.

Extracted from ``setup.py`` to keep controllers under the 800-line limit.
Handles agent personality preset assignment and preset listing.
"""

import json

from litestar import Controller, get, put
from litestar.datastructures import State  # noqa: TC002
from litestar.status_codes import HTTP_200_OK

from synthorg.api.controllers.setup_agents import (
    agent_dict_to_summary,
    get_existing_agents,
)
from synthorg.api.controllers.setup_helpers import (
    AGENT_LOCK as _AGENT_LOCK,
)
from synthorg.api.controllers.setup_helpers import (
    check_setup_not_complete as _check_setup_not_complete,
)
from synthorg.api.controllers.setup_helpers import (
    validate_agent_index as _validate_agent_index,
)
from synthorg.api.controllers.setup_models import (
    PersonalityPresetInfoResponse,
    PersonalityPresetsListResponse,
    SetupAgentSummary,
    UpdateAgentPersonalityRequest,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo, require_read_access
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_AGENT_PERSONALITY_UPDATED,
    SETUP_PERSONALITY_PRESETS_LISTED,
    SETUP_PRESET_NOT_FOUND,
)

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
        agent_index: int,
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
        """
        app_state: AppState = state.app_state
        settings_svc = app_state.settings_service
        await _check_setup_not_complete(settings_svc)

        from synthorg.templates.preset_service import (  # noqa: PLC0415
            fetch_custom_presets_map,
        )
        from synthorg.templates.presets import (  # noqa: PLC0415
            get_personality_preset,
        )

        custom_presets = await fetch_custom_presets_map(
            app_state.persistence.custom_presets,
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

        async with _AGENT_LOCK:
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
        state: State,  # noqa: ARG002
    ) -> ApiResponse[PersonalityPresetsListResponse]:
        """List all available personality presets.

        Args:
            state: Application state.

        Returns:
            Personality presets data envelope.
        """
        from synthorg.templates.presets import (  # noqa: PLC0415
            PERSONALITY_PRESETS,
        )

        presets = tuple(
            PersonalityPresetInfoResponse(
                name=name,
                description=str(preset["description"]),
            )
            for name, preset in sorted(PERSONALITY_PRESETS.items())
        )

        logger.debug(
            SETUP_PERSONALITY_PRESETS_LISTED,
            count=len(presets),
        )

        return ApiResponse(
            data=PersonalityPresetsListResponse(presets=presets),
        )
