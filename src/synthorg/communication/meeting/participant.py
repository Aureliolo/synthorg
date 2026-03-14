"""Participant resolver protocol and concrete implementation.

Resolves participant reference strings (department names, agent names,
special values like ``"all"``, literal IDs) into agent ID tuples.
"""

from typing import Any, Protocol, runtime_checkable

from synthorg.communication.meeting.errors import NoParticipantsResolvedError
from synthorg.hr.registry import AgentRegistryService  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.meeting import (
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
        """Resolve a single participant entry via context then registry.

        Args:
            entry: A participant reference string.
            ctx: Event context dict.

        Returns:
            List of agent ID strings for this entry.
        """
        if entry in ctx:
            return self._resolve_from_context(entry, ctx[entry])
        return await self._resolve_from_registry(entry)

    @staticmethod
    def _resolve_from_context(entry: str, val: Any) -> list[str]:
        """Resolve a participant entry from event context.

        Args:
            entry: The context key that matched.
            val: The context value.

        Returns:
            List of agent ID strings.
        """
        if isinstance(val, str):
            stripped = val.strip()
            if stripped:
                return [stripped]
            logger.warning(
                MEETING_NO_PARTICIPANTS,
                entry=entry,
                note="context string value is blank, skipping",
            )
            return []
        if isinstance(val, (list, tuple)):
            return [v.strip() for v in val if isinstance(v, str) and v.strip()]
        logger.warning(
            MEETING_NO_PARTICIPANTS,
            entry=entry,
            ctx_value_type=type(val).__name__,
            note="context value is not str or list, skipping",
        )
        return []

    async def _resolve_from_registry(self, entry: str) -> list[str]:
        """Resolve a participant entry via agent registry lookups.

        Resolution order: "all" → department → name → literal pass-through.

        Args:
            entry: A participant reference string.

        Returns:
            List of agent ID strings.
        """
        if entry.lower() == "all":
            agents = await self._registry.list_active()
            return [str(a.id) for a in agents]

        dept_agents = await self._registry.list_by_department(entry)
        if dept_agents:
            return [str(a.id) for a in dept_agents]

        agent = await self._registry.get_by_name(entry)
        if agent is not None:
            return [str(agent.id)]

        logger.debug(
            MEETING_PARTICIPANTS_RESOLVED,
            entry=entry,
            note="falling back to literal participant ID",
        )
        return [entry]
