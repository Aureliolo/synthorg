"""Shared agent-resolution helpers for the agents controllers.

Both the CRUD controller and the observability controller resolve an
agent from the ``{agent_id}`` path segment: ``_config_agent_by_id``
against the config resolver (CRUD reads) and
``_require_registered_identity`` against the live registry
(observability reads). The shared default page size lives here too.
"""

from typing import Final

from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState
from synthorg.config.agent_schema import AgentConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.domain_errors import NotFoundError
from synthorg.hr.state import agent_registry_of
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


async def _require_registered_identity(
    app_state: AppState,
    agent_id: str,
) -> AgentIdentity:
    """Resolve a registered agent by its stable id.

    Args:
        app_state: Application state with agent registry.
        agent_id: Stable agent id from the URL path.

    Returns:
        The registered ``AgentIdentity``.

    Raises:
        NotFoundError: If no agent with *agent_id* is registered.
    """
    # str(agent.id) is canonical lowercase, so lowercase the path segment to
    # resolve case variants -- mirrors _config_agent_by_id so the registry-
    # backed routes don't 404 on an id the config route would resolve.
    canonical_agent_id = agent_id.lower()
    return require_resource_or_404(
        await agent_registry_of(app_state).get(canonical_agent_id),
        resource_type="agent",
        identifier=canonical_agent_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation="read",
        extra_log_kwargs={"agent_id": canonical_agent_id},
    )


async def _config_agent_by_id(
    app_state: AppState,
    agent_id: str,
) -> AgentConfig:
    """Resolve a config-sourced agent by its stable id.

    Args:
        app_state: Application state with the config resolver.
        agent_id: Stable agent id from the URL path.

    Returns:
        The matching ``AgentConfig``.

    Raises:
        NotFoundError: If no configured agent has *agent_id*.
    """
    # str(agent.id) is canonical lowercase, so lowercase the path segment to
    # resolve case variants; a non-matching (or malformed) id falls through.
    target = agent_id.lower()
    agents = await config_resolver_of(app_state).get_agents()
    for agent in agents:
        if str(agent.id) == target:
            return agent
    msg = "Agent not found"
    logger.warning(API_RESOURCE_NOT_FOUND, resource="agent", agent_id=agent_id)
    raise NotFoundError(msg)
