# module-kind: controller
"""Agent-management endpoints for first-run setup.

Create, list, model-reassign, rename, and randomize-name operations on
the agents persisted during the setup wizard's Review Org step.
"""

import json
from typing import Annotated

from litestar import Controller, get, post, put
from litestar.datastructures import State
from litestar.params import PathParameter
from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED

from synthorg.api.controllers.setup.agent_helpers import (
    AGENT_LOCK as _AGENT_LOCK,
)
from synthorg.api.controllers.setup.agent_helpers import (
    COMPLETE_LOCK as _COMPLETE_LOCK,
)
from synthorg.api.controllers.setup.agent_helpers import (
    validate_agent_index as _validate_agent_index,
)
from synthorg.api.controllers.setup.company_helpers import (
    check_setup_not_complete as _check_setup_not_complete,
)
from synthorg.api.controllers.setup.company_helpers import (
    read_name_locales as _read_name_locales,
)
from synthorg.api.controllers.setup_agents import (
    agent_dict_to_summary,
    agents_to_summaries,
    build_agent_config,
    get_existing_agents,
    validate_model_assignment,
    validate_provider_and_model,
)
from synthorg.api.controllers.setup_models import (
    SetupAgentRequest,
    SetupAgentResponse,
    SetupAgentSummary,
    UpdateAgentModelRequest,
    UpdateAgentNameRequest,
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
    SETUP_AGENT_CREATED,
    SETUP_AGENT_MODEL_UPDATED,
    SETUP_AGENT_NAME_RANDOMIZED,
    SETUP_AGENT_NAME_UPDATED,
    SETUP_AGENTS_LISTED,
)
from synthorg.persistence.state import persistence_of
from synthorg.providers.state import provider_management_of
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)


class SetupAgentsController(Controller):
    """Agent create / list / update endpoints for the setup wizard."""

    path = "/setup"
    tags = ("setup",)

    @post(
        "/agent",
        status_code=HTTP_201_CREATED,
        guards=[require_ceo],
    )
    async def create_agent(
        self,
        data: SetupAgentRequest,
        state: State,
    ) -> ApiResponse[SetupAgentResponse]:
        """Create an agent during first-run setup.

        Used for the "Start Blank" path where no template is selected
        and agents are added manually from the Review Org step.

        Args:
            data: Agent creation payload.
            state: Application state.

        Returns:
            Agent creation result envelope.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the provider does not exist.
            ValidationError: If the model is not in the provider.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        from synthorg.templates.preset_service import (  # noqa: PLC0415
            fetch_custom_presets_map,
        )

        providers = await provider_management_of(app_state).list_providers()
        validate_provider_and_model(providers, data)
        custom_presets = await fetch_custom_presets_map(
            persistence_of(app_state).custom_presets,
        )
        agent_config = build_agent_config(
            data,
            custom_presets=custom_presets,
        )

        # Lock order across this module is _COMPLETE_LOCK -> _AGENT_LOCK
        # (matches ``complete_setup``). Acquiring _AGENT_LOCK alone
        # would let an in-flight ``/setup/complete`` slip in between
        # ``_check_setup_not_complete`` and the ``settings_svc.set``
        # call below, leaving the runtime bootstrap out of sync with
        # persisted agents.
        async with _COMPLETE_LOCK, _AGENT_LOCK:
            await _check_setup_not_complete(settings_svc)
            existing_agents = await get_existing_agents(settings_svc)
            updated_agents = [*existing_agents, agent_config]
            await settings_svc.set(
                "company",
                "agents",
                json.dumps(updated_agents),
            )

        logger.info(
            SETUP_AGENT_CREATED,
            agent_name=data.name,
            role=data.role,
            provider=data.model_provider,
            model=data.model_id,
        )

        return ApiResponse(
            data=SetupAgentResponse(
                name=data.name,
                role=data.role,
                department=data.department,
                model_provider=data.model_provider,
                model_id=data.model_id,
            ),
        )

    @get(
        "/agents",
        guards=[require_read_access],
    )
    async def list_agents(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[SetupAgentSummary]:
        """List agents currently configured during setup, paginated by name.

        Used by the Review Org step to display the current org and
        allow model reassignment.

        Args:
            state: Application state.
            cursor: Opaque cursor from a previous page.
            limit: Page size.

        Returns:
            Paginated agent summaries.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        agents = await get_existing_agents(settings_svc)
        # Preserve persisted-array order so that PUT/POST handlers
        # which resolve ``agent_index`` against the same array stay in
        # sync with the visible list order. Reordering here would let
        # a client update or randomize the wrong agent as soon as the
        # storage order diverges from the sorted-by-name order.
        summaries = tuple(agents_to_summaries(agents))

        page, meta = paginate_cursor(
            summaries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        logger.debug(SETUP_AGENTS_LISTED, count=len(page))
        return PaginatedResponse[SetupAgentSummary](data=page, pagination=meta)

    @put(
        "/agents/{agent_index:int}/model",
        status_code=HTTP_200_OK,
        guards=[require_ceo],
    )
    async def update_agent_model(
        self,
        agent_index: Annotated[int, PathParameter()],
        data: UpdateAgentModelRequest,
        state: State,
    ) -> ApiResponse[SetupAgentSummary]:
        """Update a single agent's model assignment during setup.

        Args:
            agent_index: Zero-based index of the agent to update.
            data: New model assignment.
            state: Application state.

        Returns:
            Updated agent summary.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the agent index is out of range.
            ValidationError: If the provider/model is invalid.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        # Validate provider/model before acquiring the lock.
        providers = await provider_management_of(app_state).list_providers()
        validate_model_assignment(providers, data)

        # Lock order: _COMPLETE_LOCK -> _AGENT_LOCK (see create_agent).
        async with _COMPLETE_LOCK, _AGENT_LOCK:
            await _check_setup_not_complete(settings_svc)
            agents = await get_existing_agents(settings_svc)
            _validate_agent_index(agent_index, agents)

            updated_agent = {
                **agents[agent_index],
                "model": {
                    "provider": data.model_provider,
                    "model_id": data.model_id,
                },
            }
            agents = [*agents[:agent_index], updated_agent, *agents[agent_index + 1 :]]
            await settings_svc.set("company", "agents", json.dumps(agents))

        logger.info(
            SETUP_AGENT_MODEL_UPDATED,
            agent_index=agent_index,
            provider=data.model_provider,
            model=data.model_id,
        )

        return ApiResponse(
            data=agent_dict_to_summary(agents[agent_index]),
        )

    @put(
        "/agents/{agent_index:int}/name",
        status_code=HTTP_200_OK,
        guards=[require_ceo],
    )
    async def update_agent_name(
        self,
        agent_index: Annotated[int, PathParameter()],
        data: UpdateAgentNameRequest,
        state: State,
    ) -> ApiResponse[SetupAgentSummary]:
        """Update a single agent's display name during setup.

        Args:
            agent_index: Zero-based index of the agent to update.
            data: New name assignment.
            state: Application state.

        Returns:
            Updated agent summary.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the agent index is out of range.
        """
        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        # Lock order: _COMPLETE_LOCK -> _AGENT_LOCK (see create_agent).
        async with _COMPLETE_LOCK, _AGENT_LOCK:
            await _check_setup_not_complete(settings_svc)
            agents = await get_existing_agents(settings_svc)
            _validate_agent_index(agent_index, agents)

            updated_agent = {
                **agents[agent_index],
                "name": data.name,
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
            SETUP_AGENT_NAME_UPDATED,
            agent_index=agent_index,
            name=data.name,
        )

        return ApiResponse(
            data=agent_dict_to_summary(agents[agent_index]),
        )

    @post(
        "/agents/{agent_index:int}/randomize-name",
        status_code=HTTP_200_OK,
        guards=[require_ceo],
    )
    async def randomize_agent_name(
        self,
        agent_index: Annotated[int, PathParameter()],
        state: State,
    ) -> ApiResponse[SetupAgentSummary]:
        """Generate a random name for an agent using locale preferences.

        Args:
            agent_index: Zero-based index of the agent to update.
            state: Application state.

        Returns:
            Updated agent summary with a new random name.

        Raises:
            ConflictError: If setup has already been completed.
            NotFoundError: If the agent index is out of range.
        """
        from synthorg.templates.presets import (  # noqa: PLC0415
            generate_auto_name,
        )

        app_state: AppState = state.app_state
        settings_svc = settings_service_of(app_state)

        locales = await _read_name_locales(settings_svc)

        # Lock order: _COMPLETE_LOCK -> _AGENT_LOCK (see create_agent).
        async with _COMPLETE_LOCK, _AGENT_LOCK:
            await _check_setup_not_complete(settings_svc)
            agents = await get_existing_agents(settings_svc)
            _validate_agent_index(agent_index, agents)

            role = agents[agent_index].get("role", "Agent")
            new_name = generate_auto_name(role, locales=locales)

            updated_agent = {
                **agents[agent_index],
                "name": new_name,
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
            SETUP_AGENT_NAME_RANDOMIZED,
            agent_index=agent_index,
            name=new_name,
        )

        return ApiResponse(
            data=agent_dict_to_summary(agents[agent_index]),
        )
