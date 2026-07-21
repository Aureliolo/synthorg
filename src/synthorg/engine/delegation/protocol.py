# module-kind: code
"""Structural contract for a blocking sub-agent runner.

The ``delegate_and_await`` tool depends on this surface, not on the
concrete :class:`~synthorg.engine.delegation.runner.InProcessSubAgentRunner`,
so tests can substitute a lightweight double and an out-of-process runner
could be wired later without touching the tool.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.delegation.models import DelegationResult, DelegationSpec


@runtime_checkable
class SubAgentRunner(Protocol):
    """Run a child agent to completion and return a bounded result."""

    async def run(
        self,
        spec: DelegationSpec,
        *,
        max_turns: int,
    ) -> DelegationResult:
        """Execute ``spec`` on a child agent, bounded by ``max_turns``.

        Raises:
            DelegationTargetNotFoundError: When ``spec.target`` resolves
                to no registered agent.
        """
        ...
