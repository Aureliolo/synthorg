"""Toolsmith feature state slice.

Holds the self-extending toolkit runtime service and its autonomous
detection scheduler. ``None`` until wired at boot (gated on
``tools.tool_creation_enabled`` plus a provider).
"""

from typing import Self

from pydantic import ConfigDict, model_validator

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.meta.toolsmith.cycle_scheduler import ToolsmithCycleScheduler
from synthorg.meta.toolsmith.service import ToolsmithService


class ToolsmithStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the toolsmith feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: ToolsmithService | None = None
    cycle_scheduler: ToolsmithCycleScheduler | None = None

    @model_validator(mode="after")
    def _service_and_scheduler_are_paired(self) -> Self:
        """Require the service and its cycle scheduler to be wired together.

        The scheduler drives ``service.run_cycle`` on a cadence, so a
        scheduler without a service (or a wired service with no scheduler
        driving it) is a wiring bug; the toolsmith boot step installs both
        or neither.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When exactly one of ``service`` / ``cycle_scheduler``
                is set.
        """
        if (self.service is None) != (self.cycle_scheduler is None):
            msg = (
                "ToolsmithStateSlice.service and .cycle_scheduler must be "
                "wired together (both set or both None); got "
                f"service={'set' if self.service else 'None'}, "
                f"cycle_scheduler={'set' if self.cycle_scheduler else 'None'}"
            )
            raise ValueError(msg)
        return self
