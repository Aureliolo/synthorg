"""Configuration for the agent evolution system.

Defines frozen Pydantic config models for each pluggable axis
(triggers, proposers, adapters, guards, memory, identity store)
with safe defaults matching the issue specification.
"""

from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.identity.store.config import (
    IdentityStoreConfig,
)
from synthorg.memory.procedural.capture.config import (
    CaptureConfig,
)
from synthorg.memory.procedural.propagation.config import (
    PropagationConfig,
)
from synthorg.memory.procedural.pruning.config import (
    PruningConfig,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
)


class TriggerConfig(BaseModel):
    """Configuration for evolution triggers.

    Attributes:
        types: Which trigger types to enable.
        batched_interval_seconds: Interval for batched trigger.
        per_task_min_tasks: Min tasks between per-task triggers.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    types: tuple[Literal["batched", "inflection", "per_task"], ...] = (
        "batched",
        "inflection",
    )
    batched_interval_seconds: int = Field(
        default=86400, ge=1, description="Seconds between batched runs"
    )
    per_task_min_tasks: int = Field(
        default=1,
        ge=1,
        description="Min tasks between per-task triggers",
    )


class ProposerConfig(BaseModel):
    """Configuration for evolution proposers.

    Attributes:
        type: Proposer strategy to use.
        model: LLM model identifier for analysis.
        temperature: Sampling temperature.
        max_tokens: Token budget for proposer response.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["separate_analyzer", "self_report", "composite"] = "composite"
    model: NotBlankStr = Field(
        default="example-small-001",
        description="Model for proposer LLM calls",
    )
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2000, ge=100)


class AdapterConfig(BaseModel):
    """Configuration for which adaptation axes are enabled.

    Attributes:
        identity: Enable identity mutations (highest risk).
        strategy_selection: Enable strategy preference changes.
        prompt_template: Enable prompt injection of learned memories.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    identity: bool = False
    strategy_selection: bool = True
    prompt_template: bool = True


class ShadowEvaluationConfig(BaseModel):
    """Configuration for the shadow evaluation guard.

    Shadow evaluation runs the adapted agent against a probe task suite
    and compares the outcome to a baseline run before approving the
    proposal.  The operator configures the task source, per-task
    timeout, and the regression tolerances.

    Attributes:
        task_provider: Which task-source strategy to use.  ``configured``
            reads ``probe_tasks``; ``recent_history`` delegates to the
            optional ``RecentTaskHistoryProvider`` wired at build time.
        probe_tasks: Curated sample tasks used by ``configured`` provider.
        sample_size: Upper bound on tasks actually executed per run.
        timeout_per_task_seconds: Hard time budget for each probe run.
        score_regression_tolerance: Acceptable drop in mean quality
            score (``baseline_mean - adapted_mean``); values above this
            reject the proposal.
        pass_rate_regression_tolerance: Acceptable drop in pass rate,
            expressed as a fraction of the baseline rate.  When baseline
            pass rate is zero the adapted pass rate must also be zero
            (otherwise we have no signal either way and reject).
        evaluator_agent_id: Identifier attached to shadow-eval telemetry
            events so operators can distinguish shadow verdicts from
            real-guard verdicts in dashboards.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_provider: Literal["configured", "recent_history"] = Field(
        default="configured",
        description="Which task-source strategy to use",
    )
    probe_tasks: tuple[Task, ...] = Field(
        default=(),
        description="Curated sample tasks (used by configured provider)",
    )
    sample_size: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Maximum tasks executed per shadow run",
    )
    timeout_per_task_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description="Hard timeout per probe task",
    )
    score_regression_tolerance: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Acceptable drop in mean quality score",
    )
    pass_rate_regression_tolerance: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Acceptable fractional drop in pass rate",
    )
    evaluator_agent_id: NotBlankStr = Field(
        default="shadow-evaluator",
        description="Agent id recorded in shadow-eval telemetry",
    )

    @model_validator(mode="after")
    def _check_provider_consistency(self) -> Self:
        """Reject configs whose ``probe_tasks`` contradicts ``task_provider``."""
        if self.task_provider == "configured" and not self.probe_tasks:
            msg = (
                "task_provider='configured' requires at least one entry in probe_tasks"
            )
            raise ValueError(msg)
        if self.task_provider == "recent_history" and self.probe_tasks:
            msg = (
                "task_provider='recent_history' must not set probe_tasks; "
                "tasks come from the history sampler instead"
            )
            raise ValueError(msg)
        return self


class GuardConfig(BaseModel):
    """Configuration for evolution guards.

    Attributes:
        review_gate: Enable human review for risky adaptations.
        rollback: Enable regression-triggered rollback.
        rollback_window_tasks: Tasks to monitor post-adaptation.
        rollback_regression_threshold: Quality drop triggering rollback.
        rate_limit: Enable per-agent rate limiting.
        rate_limit_per_day: Max adaptations per agent per day.
        shadow_evaluation: Shadow evaluation config.  ``None`` disables
            the guard; set a ``ShadowEvaluationConfig`` to enable.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    review_gate: bool = True
    rollback: bool = True
    rollback_window_tasks: int = Field(default=20, ge=1)
    rollback_regression_threshold: float = Field(default=0.1, ge=0.0, le=1.0)
    rate_limit: bool = True
    rate_limit_per_day: int = Field(default=3, ge=1)
    shadow_evaluation: ShadowEvaluationConfig | None = Field(
        default=None,
        description="Shadow evaluation config (None disables the guard)",
    )


class MemoryEvolutionConfig(BaseModel):
    """Configuration for memory-related evolution extensions.

    Attributes:
        capture: Procedural memory capture strategy config.
        pruning: Procedural memory pruning strategy config.
        propagation: Cross-agent propagation strategy config.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    pruning: PruningConfig = Field(default_factory=PruningConfig)
    propagation: PropagationConfig = Field(
        default_factory=PropagationConfig,
    )


class EvolutionConfig(BaseModel):
    """Top-level configuration for the agent evolution system.

    Safe defaults:
    - Triggers: batched (daily) + inflection
    - Proposer: composite (analyzer for failures, self-report for success)
    - Adapters: prompt_template ON, strategy_selection ON, identity OFF
    - Guards: review_gate + rollback + rate_limit ON; shadow OFF
    - Identity store: append_only
    - Propagation: none (opt-in per org)

    Attributes:
        enabled: Master switch for the evolution system.
        triggers: Trigger strategy configuration.
        proposer: Proposer strategy configuration.
        adapters: Which adaptation axes are enabled.
        guards: Guard configuration.
        memory: Memory extension configuration.
        identity_store: Identity version store configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="enabled",
            namespace=SettingNamespace.ENGINE,
            key="evolution_enabled",
            parse=parse_bool,
        ),
    )

    enabled: bool = False
    triggers: TriggerConfig = Field(default_factory=TriggerConfig)
    proposer: ProposerConfig = Field(default_factory=ProposerConfig)
    adapters: AdapterConfig = Field(default_factory=AdapterConfig)
    guards: GuardConfig = Field(default_factory=GuardConfig)
    memory: MemoryEvolutionConfig = Field(
        default_factory=MemoryEvolutionConfig,
    )
    identity_store: IdentityStoreConfig = Field(
        default_factory=IdentityStoreConfig,
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)
