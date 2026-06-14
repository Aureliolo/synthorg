"""Typed argument models for MCP workflows + organization domains.

Covers ``workflows`` / ``subworkflows`` / ``workflow_executions`` /
``workflow_versions`` (15 tools) plus ``company`` / ``company_versions``
/ ``departments`` / ``teams`` / ``role_versions`` (19 tools).
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.enums import WorkflowExecutionStatus
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

# ── Workflows ───────────────────────────────────────────────────────


class WorkflowsListArgs(PaginationFields):
    """Args for ``workflows.list``."""


class WorkflowsGetArgs(_ArgsBase):
    """Args for ``workflows.get``."""

    workflow_id: NotBlankStr = Field(description="Workflow UUID")


class WorkflowsCreateArgs(_ArgsBase):
    """Args for ``workflows.create``."""

    definition: dict[str, object] = Field(
        description="WorkflowDefinition payload",
    )


class WorkflowsUpdateArgs(_ArgsBase):
    """Args for ``workflows.update``."""

    definition: dict[str, object] = Field(
        description="WorkflowDefinition payload (including id)",
    )


class WorkflowsDeleteArgs(AdminGuardrailFields):
    """Args for ``workflows.delete`` (destructive)."""

    workflow_id: NotBlankStr = Field(description="Workflow UUID")


class WorkflowsValidateArgs(_ArgsBase):
    """Args for ``workflows.validate``."""

    definition: dict[str, object] = Field(
        description="WorkflowDefinition payload to validate",
    )


# ── Subworkflows ────────────────────────────────────────────────────


class SubworkflowsListArgs(PaginationFields):
    """Args for ``subworkflows.list``."""

    query: NotBlankStr | None = Field(
        default=None,
        description="Free-text search filter",
    )


class SubworkflowsGetArgs(_ArgsBase):
    """Args for ``subworkflows.get``."""

    subworkflow_id: NotBlankStr = Field(description="Subworkflow UUID")
    version: NotBlankStr | None = Field(
        default=None,
        description="Optional version label",
    )


class SubworkflowsCreateArgs(_ArgsBase):
    """Args for ``subworkflows.create``."""

    definition: dict[str, object] = Field(
        description="WorkflowDefinition payload",
    )


class SubworkflowsDeleteArgs(AdminGuardrailFields):
    """Args for ``subworkflows.delete`` (destructive)."""

    subworkflow_id: NotBlankStr = Field(description="Subworkflow UUID")
    version: NotBlankStr = Field(description="Version label to delete")


# ── Workflow executions ────────────────────────────────────────────


class WorkflowExecutionsListArgs(PaginationFields):
    """Args for ``workflow_executions.list``."""

    workflow_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by workflow",
    )
    status: WorkflowExecutionStatus | None = Field(
        default=None,
        description="Filter by execution status",
    )


class WorkflowExecutionsGetArgs(_ArgsBase):
    """Args for ``workflow_executions.get``."""

    execution_id: NotBlankStr = Field(description="Execution UUID")


class WorkflowExecutionsStartArgs(_ArgsBase):
    """Args for ``workflow_executions.start``."""

    workflow_id: NotBlankStr = Field(description="Workflow to execute")
    project: NotBlankStr = Field(
        default=NotBlankStr("default"),
        description="Target project",
    )
    context: dict[str, object] = Field(
        default_factory=dict,
        description="Execution context",
    )


class WorkflowExecutionsCancelArgs(AdminGuardrailFields):
    """Args for ``workflow_executions.cancel`` (destructive)."""

    execution_id: NotBlankStr = Field(description="Execution UUID")


# ── Workflow versions ─────────────────────────────────────────────


class WorkflowVersionsListArgs(PaginationFields):
    """Args for ``workflow_versions.list``."""

    workflow_id: NotBlankStr = Field(description="Workflow UUID")


class WorkflowVersionsGetArgs(_ArgsBase):
    """Args for ``workflow_versions.get``."""

    workflow_id: NotBlankStr = Field(description="Workflow UUID")
    revision: int = Field(ge=1, description="Revision number")


# ── Company ─────────────────────────────────────────────────────────


class CompanyGetArgs(_ArgsBase):
    """Args for ``company.get``: no fields."""


class CompanyUpdateArgs(_ArgsBase):
    """Args for ``company.update``."""

    payload: dict[str, object] = Field(description="Company-record patch payload")


class CompanyListDepartmentsArgs(_ArgsBase):
    """Args for ``company.list_departments``: no fields."""


class CompanyReorderDepartmentsArgs(_ArgsBase):
    """Args for ``company.reorder_departments``."""

    department_ids: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Department IDs in display order",
    )


# ── Company versions ───────────────────────────────────────────────


class CompanyVersionsListArgs(PaginationFields):
    """Args for ``company_versions.list``."""


class CompanyVersionsGetArgs(_ArgsBase):
    """Args for ``company_versions.get``."""

    version_id: NotBlankStr = Field(description="Company version ID")


# ── Departments ────────────────────────────────────────────────────


class DepartmentsListArgs(PaginationFields):
    """Args for ``departments.list``."""


class _DepartmentNameArgs(_ArgsBase):
    """Mixin for tools keyed by department ``name``."""

    name: NotBlankStr = Field(description="Department name")


class _DepartmentIdArgs(_ArgsBase):
    """Mixin for tools keyed by department UUID ``department_id``."""

    department_id: NotBlankStr = Field(description="Department ID")


class DepartmentsGetArgs(_DepartmentIdArgs):
    """Args for ``departments.get``."""


class DepartmentsCreateArgs(_DepartmentNameArgs):
    """Args for ``departments.create``."""

    description: NotBlankStr = Field(description="Department description")


class DepartmentsUpdateArgs(_DepartmentIdArgs):
    """Args for ``departments.update``."""

    name: NotBlankStr | None = Field(default=None, description="New name")
    description: NotBlankStr | None = Field(
        default=None,
        description="New description",
    )


class DepartmentsDeleteArgs(_DepartmentIdArgs, AdminGuardrailFields):
    """Args for ``departments.delete`` (destructive admin op)."""


class DepartmentsGetHealthArgs(_DepartmentIdArgs):
    """Args for ``departments.get_health``."""


# ── Teams ───────────────────────────────────────────────────────────


class TeamsListArgs(PaginationFields):
    """Args for ``teams.list``."""


class TeamsGetArgs(_ArgsBase):
    """Args for ``teams.get``."""

    team_id: NotBlankStr = Field(description="Team UUID")


class TeamsCreateArgs(_ArgsBase):
    """Args for ``teams.create``."""

    name: NotBlankStr = Field(description="Team name")
    department_id: NotBlankStr | None = Field(
        default=None,
        description="Parent department ID",
    )


class TeamsUpdateArgs(_ArgsBase):
    """Args for ``teams.update``."""

    team_id: NotBlankStr = Field(description="Team UUID")
    name: NotBlankStr | None = Field(default=None, description="New name")
    department_id: NotBlankStr | None = Field(
        default=None,
        description="New parent department ID",
    )


class TeamsDeleteArgs(AdminGuardrailFields):
    """Args for ``teams.delete`` (destructive admin op)."""

    team_id: NotBlankStr = Field(description="Team UUID")


# ── Role versions ──────────────────────────────────────────────────


class RoleVersionsListArgs(PaginationFields):
    """Args for ``role_versions.list``."""

    role_name: NotBlankStr | None = Field(
        default=None,
        description="Filter by role name",
    )


class RoleVersionsGetArgs(_ArgsBase):
    """Args for ``role_versions.get``."""

    version_id: NotBlankStr = Field(description="Role version ID")


__all__ = [
    "CompanyGetArgs",
    "CompanyListDepartmentsArgs",
    "CompanyReorderDepartmentsArgs",
    "CompanyUpdateArgs",
    "CompanyVersionsGetArgs",
    "CompanyVersionsListArgs",
    "DepartmentsCreateArgs",
    "DepartmentsDeleteArgs",
    "DepartmentsGetArgs",
    "DepartmentsGetHealthArgs",
    "DepartmentsListArgs",
    "DepartmentsUpdateArgs",
    "RoleVersionsGetArgs",
    "RoleVersionsListArgs",
    "SubworkflowsCreateArgs",
    "SubworkflowsDeleteArgs",
    "SubworkflowsGetArgs",
    "SubworkflowsListArgs",
    "TeamsCreateArgs",
    "TeamsDeleteArgs",
    "TeamsGetArgs",
    "TeamsListArgs",
    "TeamsUpdateArgs",
    "WorkflowExecutionsCancelArgs",
    "WorkflowExecutionsGetArgs",
    "WorkflowExecutionsListArgs",
    "WorkflowExecutionsStartArgs",
    "WorkflowVersionsGetArgs",
    "WorkflowVersionsListArgs",
    "WorkflowsCreateArgs",
    "WorkflowsDeleteArgs",
    "WorkflowsGetArgs",
    "WorkflowsListArgs",
    "WorkflowsUpdateArgs",
    "WorkflowsValidateArgs",
]
