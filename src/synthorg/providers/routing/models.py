"""Domain models for the routing engine."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import CapabilityLevel, NotBlankStr


class ResolvedModel(BaseModel):
    """A fully resolved model reference.

    Attributes:
        provider_name: Provider that owns this model (e.g. ``"acme-provider"``).
        model_id: Concrete model identifier (e.g. ``"acme-large-001"``).
        alias: Short alias used in routing rules, if any.
        cost_per_1k_input: Cost per 1,000 input tokens in the configured currency.
        cost_per_1k_output: Cost per 1,000 output tokens in the configured currency.
        max_context: Maximum context window size in tokens.
        estimated_latency_ms: Estimated median latency in milliseconds.
        tier: The routing tier this model is assigned to (best-effort
            classification overlaid by operator / LLM overrides), or ``None``
            when the resolver was built without tier information.
        tool_capable: Whether the model may be assigned tool-bearing agentic
            work. Defaults to ``True`` (optimistic for un-enriched models);
            populated from capability metadata when the resolver is built from
            config.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_name: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    alias: NotBlankStr | None = Field(default=None, description="Short alias")
    capability: CapabilityLevel | None = Field(
        default=None,
        description="Assigned capability rung",
    )
    tool_capable: bool = Field(
        default=True,
        description="Whether the model may execute tool-bearing agentic work",
    )
    agent_eligible: bool = Field(
        default=True,
        description=(
            "Whether the owning provider may back an agent. False mirrors the "
            "provider's ``agent_eligible=False``, so stakes routing and agent "
            "seeding exclude the model while feature calls may still use it."
        ),
    )
    cost_per_1k_input: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k input tokens in the configured currency",
    )
    cost_per_1k_output: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k output tokens in the configured currency",
    )
    max_context: int = Field(
        default=200_000,
        gt=0,
        description="Maximum context window size in tokens",
    )
    estimated_latency_ms: int | None = Field(
        default=None,
        gt=0,
        le=300_000,
        description="Estimated median latency in milliseconds",
    )

    @property
    def total_cost_per_1k(self) -> float:
        """Total cost per 1 000 tokens (input + output)."""
        return self.cost_per_1k_input + self.cost_per_1k_output


class RoutingRequest(BaseModel):
    """Inputs to a routing decision.

    Not all fields are used by every strategy:

    - **ManualStrategy** requires ``model_override``.
    - **CostAwareStrategy** uses ``task_type`` and ``remaining_budget``.
    - **FastestStrategy** uses ``task_type`` and ``remaining_budget``.
    - **SmartStrategy** uses all fields in priority order.

    Attributes:
        task_type: Task type label (e.g. ``"development"``).
        model_override: Explicit model reference for manual routing.
        remaining_budget: Per-request cost ceiling.  Compared against
            each model's ``total_cost_per_1k`` (i.e.
            ``cost_per_1k_input + cost_per_1k_output``) to filter
            models that exceed this threshold.  This is **not** a
            total session budget -- use the budget module for that.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_type: NotBlankStr | None = Field(
        default=None,
        description="Task type label",
    )
    model_override: NotBlankStr | None = Field(
        default=None,
        description="Explicit model reference for manual routing",
    )
    remaining_budget: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Per-request cost ceiling in the configured currency, compared "
            "against model total_cost_per_1k. Not a total session budget."
        ),
    )


class RoutingDecision(BaseModel):
    """Output of a routing decision.

    Attributes:
        resolved_model: The chosen model.
        strategy_used: Name of the strategy that produced this decision.
        reason: Human-readable explanation.
        fallbacks_tried: Model refs that were tried before the final choice.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    resolved_model: ResolvedModel = Field(description="The chosen model")
    strategy_used: NotBlankStr = Field(description="Strategy name")
    reason: NotBlankStr = Field(description="Human-readable explanation")
    fallbacks_tried: tuple[str, ...] = Field(
        default=(),
        description="Model refs tried before the final choice",
    )
