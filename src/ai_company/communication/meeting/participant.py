"""Participant resolver protocol and concrete implementation.

Resolves participant reference strings (department names, agent names,
special values like ``"all"``, literal IDs) into agent ID tuples.
"""

from typing import Any, Protocol, runtime_checkable

from ai_company.communication.meeting.errors import NoParticipantsResolvedError
from ai_company.hr.registry import AgentRegistryService  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.meeting import (
    MEETING_NO_PARTICIPANTS,
    MEETING_PARTICIPANTS_RESOLVED,
)

logger = get_logger(__name__)


@runtime_checkable
class ParticipantResolver(Protocol):
    """Protocol for resolving participant references to agent IDs."""

    async def resolve(
        self,
        participant_refs: tuple[str, ...],
        context: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        """Resolve participant reference strings to agent ID strings.

        Args:
            participant_refs: Participant entries from meeting config
                (department names, agent names, ``"all"``, or literal IDs).
            context: Optional event context for dynamic participants
                (e.g. ``{"author": "agent-123"}``).

        Returns:
            Deduplicated tuple of agent ID strings.

        Raises:
            NoParticipantsResolvedError: When all entries resolve to empty.
        """
        ...


class RegistryParticipantResolver:
    """Resolves participants via the agent registry.

    Resolution order per entry:
    1. Context lookup: if context has a matching key, use its value.
    2. Special value ``"all"`` → all active agents.
    3. Department lookup: if registry returns agents for the entry.
    4. Agent name lookup: if registry finds an agent by name.
    5. Pass-through: assume the entry is a literal agent ID.

    Args:
        registry: Agent registry service for lookups.
    """

    __slots__ = ("_registry",)

    def __init__(self, registry: AgentRegistryService) -> None:
        self._registry = registry

    async def resolve(
        self,
        participant_refs: tuple[str, ...],
        context: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        """Resolve participant references to agent IDs.

        Args:
            participant_refs: Participant entries to resolve.
            context: Optional event context for dynamic resolution.

        Returns:
            Deduplicated tuple of agent ID strings.
        """
        resolved: list[str] = []
        ctx = context or {}

        for entry in participant_refs:
            ids = await self._resolve_entry(entry, ctx)
            resolved.extend(ids)

        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for agent_id in resolved:
            if agent_id not in seen:
                seen.add(agent_id)
                deduped.append(agent_id)

        if deduped:
            logger.debug(
                MEETING_PARTICIPANTS_RESOLVED,
                refs=participant_refs,
                resolved_count=len(deduped),
            )
        else:
            logger.warning(
                MEETING_NO_PARTICIPANTS,
                refs=participant_refs,
            )
            msg = f"No participants resolved from refs: {participant_refs!r}"
            raise NoParticipantsResolvedError(msg)

        return tuple(deduped)

    async def _resolve_entry(
        self,
        entry: str,
        ctx: dict[str, Any],
    ) -> list[str]:
        """Resolve a single participant entry.

        Args:
            entry: A participant reference string.
            ctx: Event context dict.

        Returns:
            List of agent ID strings for this entry.
        """
        # 1. Context lookup
        if entry in ctx:
            val = ctx[entry]
            if isinstance(val, str):
                return [val]
            if isinstance(val, (list, tuple)):
                return [v for v in val if isinstance(v, str) and v.strip()]

        # 2. Special "all"
        if entry.lower() == "all":
            agents = await self._registry.list_active()
            return [str(a.id) for a in agents]

        # 3. Department lookup
        dept_agents = await self._registry.list_by_department(entry)
        if dept_agents:
            return [str(a.id) for a in dept_agents]

        # 4. Agent name lookup
        agent = await self._registry.get_by_name(entry)
        if agent is not None:
            return [str(agent.id)]

        # 5. Pass-through as literal ID
        return [entry]
