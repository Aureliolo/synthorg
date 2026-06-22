"""Typed args models for the security MCP domain.

Backs the SecOps risk-tier override tools. The two mutating tools
(``create`` / ``revoke``) extend :class:`AdminGuardrailFields` so the
``reason`` + ``confirm`` guardrail is enforced; the inherited ``reason``
doubles as the override's audit justification.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import AdminGuardrailFields

RiskTierLiteral = Literal["low", "medium", "high", "critical"]


class RiskOverrideCreateArgs(AdminGuardrailFields):
    """Args for ``security.risk_override_create`` (privileged)."""

    action_type: NotBlankStr = Field(
        description="The 'category:action' string to reclassify",
    )
    override_tier: RiskTierLiteral = Field(
        description="The new risk tier for the action type",
    )
    expires_at: AwareDatetime = Field(
        description="Mandatory override expiry (ISO 8601, timezone-aware)",
    )


class RiskOverrideRevokeArgs(AdminGuardrailFields):
    """Args for ``security.risk_override_revoke`` (privileged)."""

    override_id: NotBlankStr = Field(
        description="Identifier of the override to revoke",
    )


class RiskOverrideListArgs(BaseModel):
    """Args for ``security.risk_override_list`` (read-only; no fields)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")
