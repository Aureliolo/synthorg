"""Typed argument models for MCP ``tasks.*`` and ``activities.*`` tools.

Each model corresponds to one tool registered in
``synthorg.meta.mcp.domains.tasks.TASK_TOOLS``.
"""

from pydantic import Field

from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

# ── Tasks ───────────────────────────────────────────────────────────


class TasksListArgs(PaginationFields):
    """Args for ``tasks.list``."""

    status: TaskStatus | None = Field(default=None, description="Filter by status")
    assigned_to: NotBlankStr | None = Field(
        default=None,
        description="Filter by assigned agent",
    )
    project: NotBlankStr | None = Field(default=None, description="Filter by project")


class TasksGetArgs(_ArgsBase):
    """Args for ``tasks.get``."""

    task_id: NotBlankStr = Field(description="Task UUID")


class TasksCreateArgs(_ArgsBase):
    """Args for ``tasks.create``.

    ``task_data`` is the full :class:`CreateTaskData` payload (validated
    by the handler against that engine model); it is a polymorphic
    ``dict[str, object]`` here because its closed shape lives in the
    engine layer, not the MCP boundary.
    """

    task_data: dict[str, object] = Field(description="CreateTaskData payload")


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
    target_status: TaskStatus = Field(description="Target status")


class TasksCancelArgs(AdminGuardrailFields):
    """Args for ``tasks.cancel`` (destructive)."""

    task_id: NotBlankStr = Field(description="Task UUID")


# ── Activities ──────────────────────────────────────────────────────


class ActivitiesListArgs(PaginationFields):
    """Args for ``activities.list``."""

    project: NotBlankStr | None = Field(default=None, description="Filter by project")
    task_id: NotBlankStr | None = Field(default=None, description="Filter by task")
    window_hours: int | None = Field(
        default=None,
        ge=1,
        description="Lookback window in hours (omit for service default)",
    )


__all__ = [
    "ActivitiesListArgs",
    "TasksCancelArgs",
    "TasksCreateArgs",
    "TasksDeleteArgs",
    "TasksGetArgs",
    "TasksListArgs",
    "TasksTransitionArgs",
    "TasksUpdateArgs",
]
