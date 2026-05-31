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
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.clock import Clock
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
    from synthorg.meta.chief_of_staff.group_chat import GroupChatService
    from synthorg.meta.chief_of_staff.group_invite import GroupInviteCoordinator
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
    approval_store: ApprovalStoreProtocol | None = None,
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

    When ``invite_enabled`` is also on and an approval store is wired, a
    :class:`GroupInviteCoordinator` is constructed and injected so an
    agent may request to bring another agent in (gated by human
    consent). With the invite feature off the service runs the plain
    contribution path unchanged.

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
    invite_coordinator = _build_invite_coordinator(
        chief_of_staff_config,
        agent_registry=agent_registry,
        repositories=repositories,
        approval_store=approval_store,
        clock=clock,
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
        invite_coordinator=invite_coordinator,
    )


def _build_invite_coordinator(
    chief_of_staff_config: ChiefOfStaffConfig,
    *,
    agent_registry: AgentRegistryService,
    repositories: ConversationalRepositories,
    approval_store: ApprovalStoreProtocol | None,
    clock: Clock | None,
) -> GroupInviteCoordinator | None:
    """Construct the agent-invite coordinator when the feature is on.

    Returns ``None`` -- leaving group chat on the plain contribution
    path -- when ``invite_enabled`` is off or no approval store is wired
    (consent cannot be parked without the queue).

    Returns:
        The ``GroupInviteCoordinator`` value when present, ``None``
        otherwise.
    """
    if not chief_of_staff_config.invite_enabled or approval_store is None:
        return None
    from synthorg.meta.chief_of_staff.group_invite import (  # noqa: PLC0415
        GroupInviteCoordinator,
    )

    logger.info(API_APP_STARTUP, note="Group chat invites configured")
    return GroupInviteCoordinator(
        invite_repo=repositories.invite_repo,
        approval_store=approval_store,
        agent_registry=agent_registry,
        participant_repo=repositories.participant_repo,
        config=chief_of_staff_config,
        clock=clock,
    )


__all__ = ["build_group_chat_service"]
