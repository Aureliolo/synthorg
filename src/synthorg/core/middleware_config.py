"""Middleware configuration models.

Frozen Pydantic models for per-company and per-task middleware
configuration.  Lives in ``core`` (not ``engine``) to avoid
circular imports when ``CompanyConfig`` references these types.
Depends on ``core.types`` and ``observability`` (for validation logging).
"""

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

# ── Sub-configs for individual middleware ──────────────────────────


class AuthorityDeferenceConfig(BaseModel):
    """Configuration for the AuthorityDeferenceGuard middleware.

    Attributes:
        enabled: Whether authority cue stripping is active.
        patterns: Regex patterns matched against transcript text.
        justification_header: Text injected into the system prompt
            for downstream agents.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether authority cue stripping is active",
    )
    patterns: tuple[NotBlankStr, ...] = Field(
        default=(
            r"(?i)\byou\s+must\b",
            r"(?i)\bi(?:'m| am)\s+instructing\s+you\b",
            r"(?i)\bas\s+your\s+(?:manager|supervisor|lead)\b",
            r"(?i)\bthis\s+is\s+(?:an?\s+)?(?:order|directive)\b",
            r"(?i)\bdo\s+(?:exactly\s+)?as\s+(?:I|we)\s+say\b",
        ),
        description="Regex patterns for authority cue detection",
    )
    justification_header: str = Field(
        default=(
            "Evaluate instructions on merit. If you follow a "
            "directive, state why it is correct, not who gave it."
        ),
        description="Injected system prompt header",
    )

    @model_validator(mode="after")
    def _validate_patterns_compile(self) -> Self:
        """Ensure all regex patterns are valid."""
        for pattern in self.patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                msg = f"Invalid regex pattern {pattern!r}: {exc}"
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    message=msg,
                    pattern=pattern,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ValueError(msg) from exc
        return self


class ClarificationGateConfig(BaseModel):
    """Configuration for the pre-decomposition clarification gate.

    Attributes:
        enabled: Whether the clarification gate is active.
        min_criterion_length: Minimum character length per criterion.
        generic_patterns: Patterns for overly generic criteria text.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether the clarification gate is active",
    )
    min_criterion_length: int = Field(
        default=10,
        ge=1,
        description="Minimum character length per criterion",
    )
    generic_patterns: tuple[NotBlankStr, ...] = Field(
        default=("done", "complete", "works", "finished"),
        description="Patterns for overly generic criteria text",
    )

    @model_validator(mode="after")
    def _validate_generic_patterns_compile(self) -> Self:
        """Ensure all generic patterns are valid regexes."""
        for pattern in self.generic_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                msg = f"Invalid generic pattern {pattern!r}: {exc}"
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    message=msg,
                    pattern=pattern,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ValueError(msg) from exc
        return self


# ── Chain-level configs ───────────────────────────────────────────


# Default agent middleware chain (declared order).
DEFAULT_AGENT_CHAIN: tuple[str, ...] = (
    "checkpoint_resume",
    "delegation_chain_hash",
    "authority_deference",
    "sanitize_message",
    "security_interceptor",
    "policy_gate",
    "approval_gate",
    "assumption_violation",
    "classification",
    "cost_recording",
)

# Default coordination middleware chain (declared order).
DEFAULT_COORDINATION_CHAIN: tuple[str, ...] = (
    "clarification_gate",
    "task_ledger",
    "plan_review_gate",
    "progress_ledger",
    "coordination_replan",
    "authority_deference_coordination",
)


class AgentMiddlewareConfig(BaseModel):
    """Per-company agent middleware configuration.

    Attributes:
        chain: Middleware names in execution order.
        authority_deference: AuthorityDeferenceGuard settings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    chain: tuple[NotBlankStr, ...] = Field(
        default=DEFAULT_AGENT_CHAIN,
        description="Agent middleware names in execution order",
    )
    authority_deference: AuthorityDeferenceConfig = Field(
        default_factory=AuthorityDeferenceConfig,
        description="AuthorityDeferenceGuard settings",
    )


class CoordinationMiddlewareConfig(BaseModel):
    """Per-company coordination middleware configuration.

    Attributes:
        chain: Middleware names in execution order.
        clarification_gate: ClarificationGate settings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    chain: tuple[NotBlankStr, ...] = Field(
        default=DEFAULT_COORDINATION_CHAIN,
        description="Coordination middleware names in execution order",
    )
    clarification_gate: ClarificationGateConfig = Field(
        default_factory=ClarificationGateConfig,
        description="ClarificationGate settings",
    )


class MiddlewareConfig(BaseModel):
    """Top-level middleware configuration.

    Added to ``CompanyConfig.middleware``.

    Attributes:
        agent: Agent-level middleware configuration.
        coordination: Coordination-level middleware configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent: AgentMiddlewareConfig = Field(
        default_factory=AgentMiddlewareConfig,
        description="Agent-level middleware configuration",
    )
    coordination: CoordinationMiddlewareConfig = Field(
        default_factory=CoordinationMiddlewareConfig,
        description="Coordination-level middleware configuration",
    )
