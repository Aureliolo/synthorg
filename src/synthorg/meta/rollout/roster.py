"""Agent enumeration for rollout strategies.

Rollouts need the live list of agents to split into control/treatment
groups (A/B) or canary subsets. Wrapping the enumeration behind a
protocol keeps ``meta.rollout`` decoupled from engine internals.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger

logger = get_logger(__name__)

AgentListSource = Callable[[], Awaitable[tuple[NotBlankStr, ...]]]
"""Zero-arg coroutine returning the current agent ids."""


@runtime_checkable
class OrgRoster(Protocol):
    """Provides the live list of agent ids for the organization."""

    async def list_agent_ids(self) -> tuple[NotBlankStr, ...]:
        """Return the current roster of agent ids."""
        ...


class CallableOrgRoster:
    """Default ``OrgRoster`` that delegates to an injected coroutine.

    The service layer binds this to a method over its live agent
    registry so ``meta.rollout`` never imports engine state directly.
    """

    def __init__(self, source: AgentListSource) -> None:
        self._source = source

    async def list_agent_ids(self) -> tuple[NotBlankStr, ...]:
        """Call the injected source and return its result.

        Returns:
            Tuple of the declared element types.
        """
        return await self._source()


class NoOpOrgRoster:
    """Roster that always returns an empty tuple.

    Used as the factory default when no real roster has been injected
    yet. Makes the absence of a roster explicit instead of silently
    falling back to placeholder agent ids.
    """

    async def list_agent_ids(self) -> tuple[NotBlankStr, ...]:
        """Return an empty tuple.

        Returns:
            Tuple of the declared element types.
        """
        return ()
