"""Typed argument models for MCP ``tasks.*`` and ``activities.*`` tools.

Each model corresponds to one tool registered in
``synthorg.meta.mcp.domains.tasks.TASK_TOOLS``.
"""

from typing import Literal

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

# ── Tasks ───────────────────────────────────────────────────────────


class TasksListArgs(PaginationFields):
    """Args for ``tasks.list``."""

    status: NotBlankStr | None = Field(default=None, description="Filter by status")
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Filter by assigned agent",
    )
    project: NotBlankStr | None = Field(default=None, description="Filter by project")


class TasksGetArgs(_ArgsBase):
    """Args for ``tasks.get``."""

    task_id: NotBlankStr = Field(description="Task UUID")


class TasksCreateArgs(_ArgsBase):
    """Args for ``tasks.create``."""

    title: NotBlankStr = Field(description="Task title")
    description: str = Field(default="", description="Task description")
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Agent to assign",
    )
    project: NotBlankStr | None = Field(default=None, description="Project name")


class TasksUpdateArgs(_ArgsBase):
    """Args for ``tasks.update``."""

    task_id: NotBlankStr = Field(description="Task UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class TasksDeleteArgs(AdminGuardrailFields):
    """Args for ``tasks.delete`` (destructive)."""

    task_id: NotBlankStr = Field(description="Task UUID")


class TasksTransitionArgs(_ArgsBase):
    """Args for ``tasks.transition``."""

    task_id: NotBlankStr = Field(description="Task UUID")
    target_status: NotBlankStr = Field(description="Target status")


class TasksCancelArgs(AdminGuardrailFields):
    """Args for ``tasks.cancel`` (destructive)."""

    task_id: NotBlankStr = Field(description="Task UUID")


# ── Activities ──────────────────────────────────────────────────────


ActivityLookbackHours = Literal[24, 48, 168]


class ActivitiesListArgs(PaginationFields):
    """Args for ``activities.list``."""

    type: NotBlankStr | None = Field(
        default=None,
        description="Activity type filter",
    )
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by agent",
    )
    last_n_hours: ActivityLookbackHours | None = Field(
        default=None,
        description="Lookback hours",
    )


__all__ = [
    "ActivitiesListArgs",
    "ActivityLookbackHours",
    "TasksCancelArgs",
    "TasksCreateArgs",
    "TasksDeleteArgs",
    "TasksGetArgs",
    "TasksListArgs",
    "TasksTransitionArgs",
    "TasksUpdateArgs",
]
