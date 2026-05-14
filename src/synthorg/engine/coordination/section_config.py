"""Company-level coordination configuration from YAML.

Bridges the ``coordination:`` section in company YAML to the
per-run :class:`CoordinationConfig` used by :class:`MultiAgentCoordinator`.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import CoordinationTopology
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.routing.models import AutoTopologyConfig
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
)


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

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    # ``max_concurrency_per_wave`` intentionally omits the mirror:
    # its ``None`` Pydantic default means "unlimited", whereas the
    # registered ``coordination.max_concurrency_per_wave`` default is
    # the operator-tunable cap (5) used by the registered-setting
    # consumers.  Runtime code reads the registered value via
    # ``ConfigResolver``; the Pydantic field stays None-as-unlimited.
    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="fail_fast",
            namespace=SettingNamespace.COORDINATION,
            key="fail_fast",
            parse=parse_bool,
        ),
        MirrorField(
            field="enable_workspace_isolation",
            namespace=SettingNamespace.COORDINATION,
            key="enable_workspace_isolation",
            parse=parse_bool,
        ),
        MirrorField(
            field="base_branch",
            namespace=SettingNamespace.COORDINATION,
            key="base_branch",
        ),
    )

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

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

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
