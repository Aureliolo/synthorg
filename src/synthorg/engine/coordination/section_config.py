"""Company-level coordination configuration from YAML.

Bridges the ``coordination:`` section in company YAML to the
per-run :class:`CoordinationConfig` used by :class:`MultiAgentCoordinator`.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import CoordinationTopology
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.routing.models import AutoTopologyConfig


class CoordinationSectionConfig(BaseModel):
    """Company-level coordination configuration from YAML.

    Attributes:
        topology: Default coordination topology.
        auto_topology_rules: Rules for automatic topology selection.
        max_concurrency_per_wave: Max parallel agents per wave
            (``None`` = unlimited).
        fail_fast: Stop on first wave failure instead of continuing.
        enable_workspace_isolation: Create isolated workspaces for
            multi-agent execution.
        base_branch: Git branch to use for workspace isolation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Default coordination topology",
    )
    auto_topology_rules: AutoTopologyConfig = Field(
        default_factory=AutoTopologyConfig,
        description="Rules for automatic topology selection",
    )
    max_concurrency_per_wave: int | None = Field(
        default=None,
        ge=1,
        description="Max parallel agents per wave (None = unlimited)",
    )
    fail_fast: bool = Field(
        default=False,
        description="Stop on first wave failure",
    )
    enable_workspace_isolation: bool = Field(
        default=True,
        description="Create isolated workspaces for multi-agent execution",
    )
    base_branch: NotBlankStr = Field(
        default="main",
        description="Git branch for workspace isolation",
    )

    def to_coordination_config(
        self,
        *,
        max_concurrency_per_wave: int | None = None,
        fail_fast: bool | None = None,
    ) -> CoordinationConfig:
        """Convert to a per-run ``CoordinationConfig``.

        Request-level overrides take precedence over section defaults.

        Args:
            max_concurrency_per_wave: Override for max concurrency.
            fail_fast: Override for fail-fast behaviour.

        Returns:
            A ``CoordinationConfig`` with merged values.
        """
        return CoordinationConfig(
            max_concurrency_per_wave=(
                max_concurrency_per_wave
                if max_concurrency_per_wave is not None
                else self.max_concurrency_per_wave
            ),
            fail_fast=fail_fast if fail_fast is not None else self.fail_fast,
            enable_workspace_isolation=self.enable_workspace_isolation,
            base_branch=self.base_branch,
        )
