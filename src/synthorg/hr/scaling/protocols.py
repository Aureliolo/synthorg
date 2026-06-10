"""Scaling protocol definitions.

Runtime-checkable protocols for pluggable scaling strategies,
signal sources, triggers, and guards.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from synthorg.hr.scaling.enums import ScalingActionType
    from synthorg.hr.scaling.models import (
        ScalingContext,
        ScalingDecision,
        ScalingSignal,
    )


@runtime_checkable
class ScalingStrategy(Protocol):
    """Strategy for evaluating scaling decisions.

    Each strategy examines the current company state and proposes
    zero or more scaling actions (hire, prune, hold, no-op).
    """

    @property
    def name(self) -> NotBlankStr:
        """Strategy identifier."""
        ...

    @property
    def action_types(self) -> frozenset[ScalingActionType]:
        """Action types this strategy can produce."""
        ...

    async def evaluate(
        self,
        context: ScalingContext,
    ) -> tuple[ScalingDecision, ...]:
        """Evaluate the context and propose scaling decisions.

        Args:
            context: Aggregated company state snapshot.

        Returns:
            Zero or more proposed scaling decisions.
        """
        ...


@runtime_checkable
class ScalingSignalSource(Protocol):
    """Read-only adapter over an existing subsystem.

    Collects signals from a single domain (workload, budget,
    performance, or skill coverage).
    """

    @property
    def name(self) -> NotBlankStr:
        """Source identifier."""
        ...

    async def collect(
        self,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> tuple[ScalingSignal, ...]:
        """Collect current signal values.

        Args:
            agent_ids: Active agent IDs to query for.

        Returns:
            Collected signals from this source.
        """
        ...


@runtime_checkable
class ScalingTrigger(Protocol):
    """Controls when scaling evaluation should run."""

    @property
    def name(self) -> NotBlankStr:
        """Trigger identifier."""
        ...

    async def should_trigger(self) -> bool:
        """Check whether scaling evaluation should run now.

        Returns:
            True if evaluation should proceed.
        """
        ...

    async def record_run(self) -> None:
        """Record that an evaluation cycle completed.

        Implementations should reset any in-progress flags or
        update the last-run timestamp.
        """
        ...


@runtime_checkable
class ScalingGuard(Protocol):
    """Filters or modifies scaling decisions before execution.

    Guards are chained sequentially. Each guard receives the
    decisions from the previous guard and returns a filtered
    (or modified) subset.
    """

    @property
    def name(self) -> NotBlankStr:
        """Guard identifier."""
        ...

    async def filter(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingDecision, ...]:
        """Filter decisions through this guard.

        Args:
            decisions: Incoming decisions from previous guard.

        Returns:
            Filtered decisions (may be fewer or modified).
        """
        ...
