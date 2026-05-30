"""Client simulation configuration models.

All configuration models are frozen Pydantic models following
project conventions. Used to configure simulation runs, client
pools, requirement generators, and feedback strategies.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

# Acceptance band for the AI/human/hybrid pool-ratio sum: floating
# point makes an exact == 1.0 check brittle, so a +/-1% tolerance is
# allowed around the unit sum.
_RATIO_SUM_TOLERANCE_LOW: Final[float] = 0.99
_RATIO_SUM_TOLERANCE_HIGH: Final[float] = 1.01


class RequirementGeneratorConfig(BaseModel):
    """Configuration for requirement generation strategy.

    Attributes:
        strategy: Strategy identifier (template, llm, dataset,
            hybrid, procedural).
        template_path: Path to template file (for template strategy).
        dataset_path: Path to dataset file (for dataset strategy).
        llm_provider: Provider identifier (for llm/hybrid strategies).
        llm_model: Model identifier (for llm/hybrid strategies).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr = Field(
        default="template",
        description="Strategy identifier",
    )
    template_path: str | None = Field(
        default=None,
        description="Path to template file",
    )
    dataset_path: str | None = Field(
        default=None,
        description="Path to dataset file",
    )
    llm_provider: NotBlankStr | None = Field(
        default=None,
        description="Provider identifier for LLM strategies",
    )
    llm_model: NotBlankStr | None = Field(
        default=None,
        description="Model identifier for LLM strategies",
    )


class FeedbackConfig(BaseModel):
    """Configuration for feedback evaluation strategy.

    Attributes:
        strategy: Strategy identifier (binary, scored,
            criteria_check, adversarial).
        passing_score: Minimum score for acceptance (scored strategy).
        strictness_multiplier: Multiplier applied to client strictness.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr = Field(
        default="binary",
        description="Strategy identifier",
    )
    passing_score: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum score for acceptance",
    )
    strictness_multiplier: float = Field(
        default=1.0,
        gt=0.0,
        description="Multiplier applied to client strictness",
    )


class ClientPoolConfig(BaseModel):
    """Configuration for the client pool.

    Attributes:
        pool_size: Total number of clients in the pool.
        ai_ratio: Proportion of AI clients (0.0-1.0).
        human_ratio: Proportion of human clients (0.0-1.0).
        hybrid_ratio: Proportion of hybrid clients (0.0-1.0).
        selection_strategy: How ``ClientPoolStrategy.select_clients``
            picks from the pool. Dispatched by ``build_client_pool_strategy``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    pool_size: int = Field(
        default=10,
        gt=0,
        description="Total number of clients in the pool",
    )
    ai_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Proportion of AI clients",
    )
    human_ratio: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Proportion of human clients",
    )
    hybrid_ratio: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Proportion of hybrid clients",
    )
    selection_strategy: NotBlankStr = Field(
        default="round_robin",
        description="Pool selection strategy identifier",
    )

    @model_validator(mode="after")
    def _validate_ratio_sum(self) -> Self:
        """Ensure ratios sum to approximately 1.0.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When the AI / human / hybrid ratios do not sum to
                approximately 1.0.
        """
        total = self.ai_ratio + self.human_ratio + self.hybrid_ratio
        if not (_RATIO_SUM_TOLERANCE_LOW <= total <= _RATIO_SUM_TOLERANCE_HIGH):
            msg = (
                f"Ratios must sum to approximately 1.0, got "
                f"{self.ai_ratio} + {self.human_ratio} + "
                f"{self.hybrid_ratio} = {total}"
            )
            raise ValueError(msg)
        return self


class SimulationRunnerConfig(BaseModel):
    """Configuration for the simulation runner.

    Attributes:
        max_concurrent_tasks: Maximum tasks running in parallel.
        task_timeout_sec: Timeout for individual task completion.
        review_timeout_sec: Timeout for client review.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_concurrent_tasks: int = Field(
        default=10,
        gt=0,
        description="Maximum tasks running in parallel",
    )
    task_timeout_sec: float = Field(
        default=300.0,
        gt=0.0,
        description="Timeout for individual task completion",
    )
    review_timeout_sec: float = Field(
        default=60.0,
        gt=0.0,
        description="Timeout for client review",
    )


class ContinuousModeConfig(BaseModel):
    """Configuration for continuous simulation mode.

    Attributes:
        enabled: Whether continuous mode is active.
        request_interval_sec: Seconds between requirement batches.
        max_concurrent_requests: Maximum parallel requests.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether continuous mode is active",
    )
    request_interval_sec: float = Field(
        default=300.0,
        gt=0.0,
        description="Seconds between requirement batches",
    )
    max_concurrent_requests: int = Field(
        default=5,
        gt=0,
        description="Maximum parallel requests",
    )


class ReportConfig(BaseModel):
    """Configuration for simulation report output.

    Attributes:
        strategy: Report format identifier dispatched by
            ``build_report_strategy``: ``summary``, ``detailed``,
            ``json_export``, or ``metrics_only``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr = Field(
        default="summary",
        description="Report format identifier",
    )


class IntakeConfig(BaseModel):
    """Configuration for the intake strategy.

    Attributes:
        strategy: Strategy identifier dispatched by
            ``build_intake_strategy``: ``direct`` (no LLM, creates a
            task per accepted request) or ``agent`` (LLM-driven triage
            via a completion provider).
        model: Model identifier passed to the agent intake strategy.
            Only consulted when ``strategy == "agent"``; ignored by
            the ``direct`` strategy.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr = Field(
        default="direct",
        description="Intake strategy identifier (direct or agent)",
    )
    model: NotBlankStr | None = Field(
        default=None,
        description="Model id for the agent intake strategy",
    )


class ClientSimulationConfig(BaseModel):
    """Top-level client simulation configuration.

    Composes all sub-configurations into a single frozen model.

    Attributes:
        pool: Client pool configuration.
        generators: Requirement generator configuration.
        feedback: Feedback strategy configuration.
        report: Report format configuration.
        runner: Simulation runner configuration.
        continuous: Continuous mode configuration.
        intake: Intake strategy configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    pool: ClientPoolConfig = Field(
        default_factory=ClientPoolConfig,
        description="Client pool configuration",
    )
    generators: RequirementGeneratorConfig = Field(
        default_factory=RequirementGeneratorConfig,
        description="Requirement generator configuration",
    )
    feedback: FeedbackConfig = Field(
        default_factory=FeedbackConfig,
        description="Feedback strategy configuration",
    )
    report: ReportConfig = Field(
        default_factory=ReportConfig,
        description="Report format configuration",
    )
    runner: SimulationRunnerConfig = Field(
        default_factory=SimulationRunnerConfig,
        description="Simulation runner configuration",
    )
    continuous: ContinuousModeConfig = Field(
        default_factory=ContinuousModeConfig,
        description="Continuous mode configuration",
    )
    intake: IntakeConfig = Field(
        default_factory=IntakeConfig,
        description="Intake strategy configuration",
    )
