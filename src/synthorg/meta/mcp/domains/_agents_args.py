"""Typed argument models for the MCP ``agents`` domain.

Covers agents CRUD + observability + personalities + autonomy.
"""

from typing import Literal

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

# ── Agents CRUD ─────────────────────────────────────────────────────


class AgentsListArgs(PaginationFields):
    """Args for ``agents.list``."""


class _AgentNameArgs(_ArgsBase):
    """Internal mixin for tools keyed by ``agent_name``."""

    agent_name: NotBlankStr = Field(description="Agent name")


class AgentsGetArgs(_AgentNameArgs):
    """Args for ``agents.get``."""


class AgentsCreateArgs(AdminGuardrailFields):
    """Args for ``agents.create`` (creates a principal).

    ``identity`` is the full :class:`AgentIdentity` payload, validated by
    the handler against that model; it is a polymorphic ``dict[str,
    object]`` here because its closed shape lives in ``synthorg.core.agent``.

    Guardrailed like its ``agents.delete`` sibling: this call mints a
    durable organisational principal that holds a role, spends budget and
    can be selected to judge other agents' work, which is at least as
    consequential as removing one.
    """

    identity: dict[str, object] = Field(description="AgentIdentity payload")


class AgentsUpdateArgs(_ArgsBase):
    """Args for ``agents.update``."""

    agent_id: NotBlankStr = Field(description="Agent ID")
    updates: dict[str, object] = Field(description="Fields to update")


class AgentsDeleteArgs(_AgentNameArgs, AdminGuardrailFields):
    """Args for ``agents.delete`` (destructive)."""


# ── Agent observability ────────────────────────────────────────────


class AgentsGetPerformanceArgs(_AgentNameArgs):
    """Args for ``agents.get_performance``."""


class AgentsGetActivityArgs(_AgentNameArgs, PaginationFields):
    """Args for ``agents.get_activity``."""


class AgentsGetHistoryArgs(_AgentNameArgs, PaginationFields):
    """Args for ``agents.get_history``."""


class AgentsGetHealthArgs(_AgentNameArgs):
    """Args for ``agents.get_health``."""


# ── Personalities ───────────────────────────────────────────────────


class PersonalitiesListArgs(PaginationFields):
    """Args for ``personalities.list``."""


class PersonalitiesGetArgs(_ArgsBase):
    """Args for ``personalities.get``."""

    name: NotBlankStr = Field(description="Personality name")


# ── Autonomy ───────────────────────────────────────────────────────


AutonomyLevel = Literal["full", "semi", "supervised", "locked"]


class AutonomyGetArgs(_ArgsBase):
    """Args for ``autonomy.get``."""

    agent_id: NotBlankStr = Field(description="Agent ID")


class AutonomyUpdateArgs(_ArgsBase):
    """Args for ``autonomy.update``.

    ``reason`` requires at least 3 non-whitespace characters after
    stripping, enforced as a Pydantic ``min_length`` (the field's
    :class:`NotBlankStr` strip behaviour handles leading/trailing
    whitespace before the length check).
    """

    agent_id: NotBlankStr = Field(description="Agent ID")
    level: AutonomyLevel = Field(description="New autonomy level")
    reason: NotBlankStr = Field(
        min_length=3,
        description="Why the change is requested",
    )


__all__ = [
    "AgentsCreateArgs",
    "AgentsDeleteArgs",
    "AgentsGetActivityArgs",
    "AgentsGetArgs",
    "AgentsGetHealthArgs",
    "AgentsGetHistoryArgs",
    "AgentsGetPerformanceArgs",
    "AgentsListArgs",
    "AgentsUpdateArgs",
    "AutonomyGetArgs",
    "AutonomyLevel",
    "AutonomyUpdateArgs",
    "PersonalitiesGetArgs",
    "PersonalitiesListArgs",
]
