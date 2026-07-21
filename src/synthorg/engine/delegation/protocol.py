# module-kind: code
"""Structural contract for a blocking sub-agent runner.

The ``delegate_and_await`` tool depends on this surface, not on the
concrete :class:`~synthorg.engine.delegation.runner.InProcessSubAgentRunner`,
so tests can substitute a lightweight double and an out-of-process runner
could be wired later without touching the tool.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.delegation.models import (
    SubAgentDelegationResult,
    SubAgentDelegationSpec,
)


@runtime_checkable
class SubAgentRunner(Protocol):
    """Run a child agent to completion and return a bounded result."""

    async def run(
        self,
        spec: SubAgentDelegationSpec,
        *,
        max_turns: int,
        max_depth: int = ...,
        timeout_seconds: float | None = None,
    ) -> SubAgentDelegationResult:
        """Execute ``spec`` on a child agent, bounded by ``max_turns``.

        Args:
            spec: The delegation request (target, task, lineage).
            max_turns: Turn budget handed to the child run.
            max_depth: Chain-depth cap; the call is refused once the
                delegation chain reaches it.
            timeout_seconds: Optional wall-clock bound on the child run;
                ``None`` leaves it unbounded.

        Raises:
            SubAgentDelegationTargetNotFoundError: When ``spec.target``
                resolves to no registered agent.
            SubAgentDelegationDepthExceededError: When the delegation
                chain is already at ``max_depth`` or the target would
                form a cycle.
        """
        ...
