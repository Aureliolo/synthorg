"""Pluggable velocity calculator protocol.

Defines the ``VelocityCalculator`` runtime-checkable protocol that
strategy-specific velocity implementations must satisfy.

See ``docs/design/ceremony-scheduling.md`` for the full design.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from synthorg.engine.workflow.sprint_velocity import (
    VelocityRecord,
)
from synthorg.engine.workflow.velocity_types import (
    VelocityCalcType,
    VelocityMetrics,
)


@runtime_checkable
class VelocityCalculator(Protocol):
    """Pluggable velocity computation.

    Each ``CeremonySchedulingStrategy`` ships a default calculator.
    Users can override via ``CeremonyPolicyConfig.velocity_calculator``.

    Implementations must provide:

    - ``compute``: compute velocity from a single sprint record.
    - ``rolling_average``: compute rolling average over recent sprints.
    - ``calculator_type``: return the calculator type enum value.
    - ``primary_unit``: return the human-readable unit label.
    """

    def compute(self, record: VelocityRecord) -> VelocityMetrics:
        """Compute velocity metrics from a single sprint record.

        Args:
            record: A completed sprint's velocity record.

        Returns:
            Computed velocity metrics with primary value and unit.
        """
        ...

    def rolling_average(
        self,
        records: Sequence[VelocityRecord],
        window: int,
    ) -> VelocityMetrics:
        """Compute rolling average over recent sprints.

        Uses the last *window* records (by position).

        Args:
            records: Ordered velocity records (oldest first).
            window: Number of recent sprints to average over.

        Returns:
            Averaged velocity metrics.
        """
        ...

    @property
    def calculator_type(self) -> VelocityCalcType:
        """Return the calculator type enum value."""
        ...

    @property
    def primary_unit(self) -> str:
        """Return the human-readable primary unit label."""
        ...
