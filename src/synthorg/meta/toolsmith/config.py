"""Configuration for the self-extending toolkit (toolsmith).

Frozen Pydantic config with safe defaults: disabled by default, an empty
capability allowlist (deny-all until an operator opts in), Docker sandbox
with no network, and a benchmark gate that requires the golden-scorecard
no-regression check.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.models import ToolSandboxBackend


class ToolAuthoringConfig(BaseModel):
    """LLM settings for authoring a tool blueprint from a capability gap.

    Attributes:
        model: LLM model identifier for blueprint generation.
        temperature: Sampling temperature (low for structured output).
        max_tokens: Token budget per authoring response.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model: NotBlankStr = Field(default=NotBlankStr("example-large-001"))
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4000, ge=100)


class ToolValidationConfig(BaseModel):
    """Benchmark-gate settings for trusting an authored tool.

    Attributes:
        require_golden_delta: When ``True`` the golden-company scorecard
            must not regress (``candidate >= baseline``) in addition to the
            per-tool brief passing.
        min_score_margin: Minimum ``candidate - baseline`` scorecard margin
            required to pass (0 = no regression allowed).
        brief_pass_score: Minimum per-tool acceptance brief score to pass.
        golden_scorecard_provider: Which golden-scorecard provider to wire.
            ``none`` (the default) wires no provider, so a
            ``require_golden_delta`` gate fails closed; ``eval`` wires the
            eval-backed :class:`EvalGoldenScorecardProvider` so the gate
            runs the golden suite end-to-end.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    require_golden_delta: bool = True
    min_score_margin: int = Field(default=0, ge=0)
    brief_pass_score: int = Field(default=70, ge=0, le=100)
    golden_scorecard_provider: Literal["none", "eval"] = "none"


class ToolsmithConfig(BaseModel):
    """Top-level configuration for the self-extending toolkit.

    Safe defaults:
    - Feature: disabled (opt-in).
    - Capability allowlist: empty (deny-all until configured).
    - Sandbox: Docker, no network.
    - Validation: golden no-regression delta required.

    Attributes:
        enabled: Master switch for the toolsmith.
        gap_recurrence_threshold: How many times a gap signature must
            recur within the window before a proposal is triggered.
        gap_window_hours: Sliding window for gap recurrence aggregation.
        gap_buffer_size: Ring-buffer capacity for raw gap observations.
        allowed_capabilities: Capability tags (``domain:action``) the org
            may author tools for; empty denies all.
        service_access_capabilities: Capability tags that require internal
            service-layer access and so cannot be a sandbox script; gaps
            matching these route to the CODE_MODIFICATION overflow arm.
        sandbox_backend: Default sandbox backend for authored tools.
        requires_network: Whether authored tools get network egress.
        max_active_tools: Cap on simultaneously-active authored tools.
        authoring: LLM authoring settings.
        validation: Benchmark-gate settings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    gap_recurrence_threshold: int = Field(default=3, ge=2)
    gap_window_hours: int = Field(default=24, ge=1)
    gap_buffer_size: int = Field(default=512, ge=16)
    allowed_capabilities: tuple[NotBlankStr, ...] = ()
    service_access_capabilities: tuple[NotBlankStr, ...] = ()
    sandbox_backend: ToolSandboxBackend = ToolSandboxBackend.DOCKER
    requires_network: bool = False
    max_active_tools: int = Field(default=50, ge=1)
    authoring: ToolAuthoringConfig = Field(default_factory=ToolAuthoringConfig)
    validation: ToolValidationConfig = Field(default_factory=ToolValidationConfig)

    @model_validator(mode="after")
    def _enabled_requires_allowlist(self) -> Self:
        """Enable+empty-allowlist is silently deny-all; reject it explicitly.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.enabled and not self.allowed_capabilities:
            msg = (
                "ToolsmithConfig.enabled=True requires a non-empty "
                "allowed_capabilities; an empty allowlist is silently deny-all"
            )
            raise ValueError(msg)
        return self
