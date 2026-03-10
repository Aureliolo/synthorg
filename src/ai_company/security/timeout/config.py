"""Timeout policy configuration models — discriminated union of 4 policies."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

from ai_company.core.enums import TimeoutActionType
from ai_company.core.types import NotBlankStr  # noqa: TC001


class WaitForeverConfig(BaseModel):
    """Wait indefinitely for human approval — the default.

    Attributes:
        policy: Discriminator tag.
    """

    model_config = ConfigDict(frozen=True)

    policy: Literal["wait"] = "wait"


class DenyOnTimeoutConfig(BaseModel):
    """Deny the action after a fixed timeout.

    Attributes:
        policy: Discriminator tag.
        timeout_minutes: Minutes before auto-deny.
    """

    model_config = ConfigDict(frozen=True)

    policy: Literal["deny"] = "deny"
    timeout_minutes: float = Field(
        default=240.0,
        gt=0,
        description="Minutes before auto-deny",
    )


class TierConfig(BaseModel):
    """Per-risk-tier timeout configuration.

    Attributes:
        timeout_minutes: Minutes before the on_timeout action.
        on_timeout: What to do when the tier times out.
        actions: Optional set of specific action types in this tier
            (if empty, the tier is matched by risk level).
    """

    model_config = ConfigDict(frozen=True)

    timeout_minutes: float = Field(
        gt=0,
        description="Minutes before the timeout action",
    )
    on_timeout: TimeoutActionType = Field(
        default=TimeoutActionType.DENY,
        description="Action when this tier times out",
    )
    actions: tuple[str, ...] = Field(
        default=(),
        description="Specific action types in this tier",
    )


class TieredTimeoutConfig(BaseModel):
    """Per-risk-tier timeout policy.

    Each tier defines its own timeout and action. Unknown risk
    tiers fall back to HIGH (fail-safe per D19).

    Attributes:
        policy: Discriminator tag.
        tiers: Tier configurations keyed by risk level name.
    """

    model_config = ConfigDict(frozen=True)

    policy: Literal["tiered"] = "tiered"
    tiers: dict[str, TierConfig] = Field(
        default_factory=dict,
        description="Tier configs keyed by risk level (low/medium/high/critical)",
    )


class EscalationStep(BaseModel):
    """A single step in an escalation chain.

    Attributes:
        role: The role to escalate to at this step.
        timeout_minutes: Minutes to wait at this step before
            moving to the next.
    """

    model_config = ConfigDict(frozen=True)

    role: NotBlankStr = Field(description="Escalation target role")
    timeout_minutes: float = Field(
        gt=0,
        description="Minutes to wait at this escalation step",
    )


class EscalationChainConfig(BaseModel):
    """Escalation chain timeout policy.

    Approval is escalated through a chain of roles, each with its
    own timeout. If the entire chain is exhausted, the
    ``on_chain_exhausted`` action is taken.

    Attributes:
        policy: Discriminator tag.
        chain: Ordered escalation steps.
        on_chain_exhausted: Action when all steps exhaust.
    """

    model_config = ConfigDict(frozen=True)

    policy: Literal["escalation"] = "escalation"
    chain: tuple[EscalationStep, ...] = Field(
        default=(),
        description="Ordered escalation steps",
    )
    on_chain_exhausted: TimeoutActionType = Field(
        default=TimeoutActionType.DENY,
        description="Action when the entire chain is exhausted",
    )


def _timeout_discriminator(value: object) -> str:
    """Extract the ``policy`` discriminator from raw or model data."""
    if isinstance(value, dict):
        return str(value.get("policy", "wait"))
    return getattr(value, "policy", "wait")


ApprovalTimeoutConfig = Annotated[
    Annotated[WaitForeverConfig, Tag("wait")]
    | Annotated[DenyOnTimeoutConfig, Tag("deny")]
    | Annotated[TieredTimeoutConfig, Tag("tiered")]
    | Annotated[EscalationChainConfig, Tag("escalation")],
    Discriminator(_timeout_discriminator),
]
"""Discriminated union of the four timeout policy configurations."""
