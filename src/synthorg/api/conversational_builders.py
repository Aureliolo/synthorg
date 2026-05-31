# module-kind: code
"""Builders for the conversational write-path services.

Constructs the multi-agent group chat (#1970) from config + wiring.
Kept out of :mod:`synthorg.api.app_builders` so the conversational
write-path builders grow cohesively (the direct-MCP actor builder joins
here) without pushing the general builder collection past its size tier.
Mirrors the lazy-import discipline of ``app_builders``: annotations
resolve under ``TYPE_CHECKING``; runtime constructors are imported in
function bodies to avoid the budget/observability import cycle.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.clock import Clock
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
    from synthorg.meta.chief_of_staff.group_chat import GroupChatService
    from synthorg.persistence.conversational_factory import (
        ConversationalRepositories,
    )
    from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


def build_group_chat_service(  # noqa: PLR0913 -- DI builder seam
    chief_of_staff_config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    agent_registry: AgentRegistryService,
    repositories: ConversationalRepositories | None,
    cost_tracker: CostTracker | None,
    clock: Clock | None = None,
) -> GroupChatService | None:
    """Resolve a GroupChatService from config + wiring (#1970).

    Returns ``None`` -- and ``POST /meta/chat/group`` then surfaces
    503 -- when:

    - ``chief_of_staff_config.group_chat_enabled`` is False (opt-in
      default), or
    - no LLM provider is registered, or
    - the conversational repositories could not be built (persistence
      absent / not connected).

    Per-agent dispatch reuses ``build_meeting_agent_caller`` so each
    participant answers on its own configured provider; participant
    identities are resolved against *agent_registry*.

    Returns:
        The ``GroupChatService`` value when present, ``None`` otherwise.
    """
    from synthorg.communication.meeting.agent_caller import (  # noqa: PLC0415
        build_meeting_agent_caller,
    )
    from synthorg.meta.chief_of_staff.group_chat import (  # noqa: PLC0415
        GroupChatService,
    )

    if not chief_of_staff_config.group_chat_enabled:
        return None
    if repositories is None:
        logger.warning(
            API_APP_STARTUP,
            note="Group chat enabled but persistence unavailable",
        )
        return None
    if not provider_registry.list_providers():
        logger.warning(
            API_APP_STARTUP,
            note="Group chat enabled but no providers registered",
        )
        return None

    agent_caller = build_meeting_agent_caller(
        agent_registry=agent_registry,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
    )
    logger.info(API_APP_STARTUP, note="Group chat configured")
    return GroupChatService(
        agent_caller=agent_caller,
        agent_registry=agent_registry,
        config=chief_of_staff_config,
        conversation_repo=repositories.conversation_repo,
        turn_repo=repositories.turn_repo,
        participant_repo=repositories.participant_repo,
        clock=clock,
        cost_tracker=cost_tracker,
    )


__all__ = ["build_group_chat_service"]
