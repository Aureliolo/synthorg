"""Typed argument models for the MCP ``agents`` domain.

Covers agents CRUD + observability + personalities + training +
autonomy + collaboration.
"""

from typing import Literal

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
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


class AgentsCreateArgs(_ArgsBase):
    """Args for ``agents.create``."""

    name: NotBlankStr = Field(description="Agent name")
    role: NotBlankStr = Field(description="Agent role")
    department: NotBlankStr = Field(description="Department name")


class AgentsUpdateArgs(_AgentNameArgs):
    """Args for ``agents.update``."""

    updates: dict[str, object] = Field(description="Fields to update")


class AgentsDeleteArgs(_AgentNameArgs, AdminGuardrailFields):
    """Args for ``agents.delete`` (destructive)."""


# ── Agent observability ────────────────────────────────────────────


class AgentsGetPerformanceArgs(_AgentNameArgs):
    """Args for ``agents.get_performance``."""


class AgentsGetActivityArgs(_AgentNameArgs, PaginationFields):
    """Args for ``agents.get_activity``."""


class AgentsGetHistoryArgs(_AgentNameArgs):
    """Args for ``agents.get_history``."""


class AgentsGetHealthArgs(_AgentNameArgs):
    """Args for ``agents.get_health``."""


# ── Personalities ───────────────────────────────────────────────────


class PersonalitiesListArgs(PaginationFields):
    """Args for ``personalities.list``."""


class PersonalitiesGetArgs(_ArgsBase):
    """Args for ``personalities.get``."""

    name: NotBlankStr = Field(description="Personality name")


# ── Training ────────────────────────────────────────────────────────


SeniorityLevel = Literal["junior", "mid", "senior"]
TrainingContentType = Literal["procedural", "semantic", "tool_patterns"]


class TrainingListSessionsArgs(PaginationFields):
    """Args for ``training.list_sessions``."""


class TrainingGetSessionArgs(_ArgsBase):
    """Args for ``training.get_session``."""

    session_id: NotBlankStr = Field(description="Training session ID")


class TrainingStartSessionArgs(_ArgsBase):
    """Args for ``training.start_session``."""

    new_agent_id: NotBlankStr = Field(description="ID of the agent being trained")
    new_agent_role: NotBlankStr = Field(description="Role of the new hire")
    new_agent_level: SeniorityLevel = Field(
        description="Seniority level of the new hire"
    )
    new_agent_department: NotBlankStr | None = Field(
        default=None,
        description="Department of the new hire (optional)",
    )
    enabled_content_types: tuple[TrainingContentType, ...] = Field(
        default=(),
        description="Content extractors to run (defaults to all when empty)",
    )


# ── Autonomy ───────────────────────────────────────────────────────


AutonomyLevel = Literal["full", "semi", "supervised", "locked"]


class AutonomyGetArgs(_ArgsBase):
    """Args for ``autonomy.get``."""

    agent_id: NotBlankStr = Field(description="Agent ID")


class AutonomyUpdateArgs(_ArgsBase):
    """Args for ``autonomy.update``.

    ``reason`` requires at least 3 non-whitespace characters after
    stripping; the legacy JSON Schema pattern is mirrored as a
    Pydantic ``min_length`` (we let the field's
    :class:`NotBlankStr` strip behaviour handle leading/trailing
    whitespace before the length check).
    """

    agent_id: NotBlankStr = Field(description="Agent ID")
    level: AutonomyLevel = Field(description="New autonomy level")
    reason: NotBlankStr = Field(
        min_length=3,
        description="Why the change is requested",
    )


# ── Collaboration ──────────────────────────────────────────────────


class CollaborationGetScoreArgs(_ArgsBase):
    """Args for ``collaboration.get_score``."""

    agent_id: NotBlankStr = Field(description="Agent ID")


class CollaborationGetCalibrationArgs(_ArgsBase):
    """Args for ``collaboration.get_calibration``."""

    agent_id: NotBlankStr = Field(description="Agent ID")


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
    "CollaborationGetCalibrationArgs",
    "CollaborationGetScoreArgs",
    "PersonalitiesGetArgs",
    "PersonalitiesListArgs",
    "SeniorityLevel",
    "TrainingContentType",
    "TrainingGetSessionArgs",
    "TrainingListSessionsArgs",
    "TrainingStartSessionArgs",
]
