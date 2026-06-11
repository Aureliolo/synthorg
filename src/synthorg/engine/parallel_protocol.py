"""Structural contract for the parallel agent-execution orchestrator.

The coordination dispatchers depend only on the orchestrator's
``execute_group`` entry point, not on the concrete
:class:`~synthorg.engine.parallel.ParallelExecutor` (which welds in the heavy
``AgentEngine``). Annotating against this ``@runtime_checkable`` Protocol lets a
dispatcher hold the executor by its structural surface, so both the real class
and the autospec test doubles satisfy it without the concrete import.
"""

from typing import Protocol, runtime_checkable

from synthorg.engine.parallel_models import (
    ParallelExecutionGroup,
    ParallelExecutionResult,
)


@runtime_checkable
class ParallelExecutorProtocol(Protocol):
    """The single operation coordination needs: run one parallel group."""

    async def execute_group(
        self,
        group: ParallelExecutionGroup,
    ) -> ParallelExecutionResult:
        """Run every assignment in *group* concurrently and collect outcomes."""
        ...
