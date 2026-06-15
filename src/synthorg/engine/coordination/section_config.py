"""Company-level coordination configuration from YAML.

Bridges the ``coordination:`` section in company YAML to the
per-run :class:`CoordinationConfig` used by :class:`MultiAgentCoordinator`.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.task_enums import CoordinationTopology
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.routing.models import AutoTopologyConfig
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_int,
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
        decomposition_model: LLM model identifier for the
            coordinator's task decomposition strategy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="max_concurrency_per_wave",
            namespace=SettingNamespace.COORDINATION,
            key="max_concurrency_per_wave",
            parse=parse_int,
            only_if_env_set=True,
        ),
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
        MirrorField(
            field="decomposition_model",
            namespace=SettingNamespace.COORDINATION,
            key="decomposition_model",
        ),
        MirrorField(
            field="max_stall_count",
            namespace=SettingNamespace.COORDINATION,
            key="max_stall_count",
            parse=parse_int,
            only_if_env_set=True,
        ),
        MirrorField(
            field="max_reset_count",
            namespace=SettingNamespace.COORDINATION,
            key="max_reset_count",
            parse=parse_int,
            only_if_env_set=True,
        ),
        MirrorField(
            field="enable_coordination_middleware",
            namespace=SettingNamespace.COORDINATION,
            key="enable_coordination_middleware",
            parse=parse_bool,
        ),
        MirrorField(
            field="replan_strategy",
            namespace=SettingNamespace.COORDINATION,
            key="replan_strategy",
        ),
        MirrorField(
            field="orchestrator_strategy",
            namespace=SettingNamespace.COORDINATION,
            key="orchestrator_strategy",
        ),
        MirrorField(
            field="max_delegation_rounds",
            namespace=SettingNamespace.COORDINATION,
            key="max_delegation_rounds",
            parse=parse_int,
            only_if_env_set=True,
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
    decomposition_model: NotBlankStr = Field(
        default=NotBlankStr("example-medium-001"),
        description=(
            "LLM model identifier used by the coordinator's task "
            "decomposition strategy. Resolved against the first "
            "registered provider at boot. Overridable via the "
            "SYNTHORG_COORDINATION_DECOMPOSITION_MODEL environment "
            "variable (precedence: DB > env > this code default), "
            "applied on the next coordinator rebuild."
        ),
    )
    max_stall_count: int = Field(
        default=3,
        ge=1,
        description="Max consecutive stalls before the coordinator escalates",
    )
    max_reset_count: int = Field(
        default=2,
        ge=1,
        description="Max replan cycles before the coordinator escalates",
    )
    enable_coordination_middleware: bool = Field(
        default=False,
        description=(
            "Build and run the coordination middleware pipeline. Off by "
            "default so wiring it in preserves current behaviour exactly."
        ),
    )
    replan_strategy: Literal["noop", "magentic"] = Field(
        default="noop",
        description="Replan hook the middleware pipeline runs",
    )
    orchestrator_strategy: Literal["naive", "magentic_dynamic"] = Field(
        default="naive",
        description="Subtask-selection strategy for centralized dispatch",
    )
    max_delegation_rounds: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Soft cap on delegation rounds; hard abort at 2x",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
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
            max_stall_count=self.max_stall_count,
            max_reset_count=self.max_reset_count,
            replan_strategy=self.replan_strategy,
            orchestrator_strategy=self.orchestrator_strategy,
            max_delegation_rounds=self.max_delegation_rounds,
        )
