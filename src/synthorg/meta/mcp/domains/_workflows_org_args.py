"""Typed argument models for MCP workflows + organization domains.

Covers ``workflows`` / ``subworkflows`` / ``workflow_executions`` /
``workflow_versions`` (15 tools) plus ``company`` / ``company_versions``
/ ``departments`` / ``teams`` / ``role_versions`` (19 tools).
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
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

    name: NotBlankStr = Field(description="Workflow name")
    steps: tuple[dict[str, object], ...] = Field(
        description="Workflow step definitions",
    )


class WorkflowsUpdateArgs(_ArgsBase):
    """Args for ``workflows.update``."""

    workflow_id: NotBlankStr = Field(description="Workflow UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class WorkflowsDeleteArgs(AdminGuardrailFields):
    """Args for ``workflows.delete`` (destructive)."""

    workflow_id: NotBlankStr = Field(description="Workflow UUID")


class WorkflowsValidateArgs(_ArgsBase):
    """Args for ``workflows.validate``."""

    workflow_id: NotBlankStr = Field(description="Workflow UUID")


# ── Subworkflows ────────────────────────────────────────────────────


class SubworkflowsListArgs(PaginationFields):
    """Args for ``subworkflows.list``."""

    workflow_id: NotBlankStr = Field(description="Parent workflow UUID")


class SubworkflowsGetArgs(_ArgsBase):
    """Args for ``subworkflows.get``."""

    subworkflow_id: NotBlankStr = Field(description="Subworkflow UUID")


class SubworkflowsCreateArgs(_ArgsBase):
    """Args for ``subworkflows.create``."""

    workflow_id: NotBlankStr = Field(description="Parent workflow UUID")
    name: NotBlankStr = Field(description="Subworkflow name")
    steps: tuple[dict[str, object], ...] = Field(
        default=(),
        description="Step definitions",
    )


class SubworkflowsDeleteArgs(AdminGuardrailFields):
    """Args for ``subworkflows.delete`` (destructive)."""

    subworkflow_id: NotBlankStr = Field(description="Subworkflow UUID")


# ── Workflow executions ────────────────────────────────────────────


class WorkflowExecutionsListArgs(PaginationFields):
    """Args for ``workflow_executions.list``."""

    workflow_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by workflow",
    )
    status: NotBlankStr | None = Field(
        default=None,
        description="Filter by execution status",
    )


class WorkflowExecutionsGetArgs(_ArgsBase):
    """Args for ``workflow_executions.get``."""

    execution_id: NotBlankStr = Field(description="Execution UUID")


class WorkflowExecutionsStartArgs(_ArgsBase):
    """Args for ``workflow_executions.start``."""

    workflow_id: NotBlankStr = Field(description="Workflow to execute")
    parameters: dict[str, object] = Field(
        default_factory=dict,
        description="Execution parameters",
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
    version_num: int = Field(ge=1, description="Version number")


# ── Company ─────────────────────────────────────────────────────────


class CompanyGetArgs(_ArgsBase):
    """Args for ``company.get``: no fields."""


class CompanyUpdateArgs(_ArgsBase):
    """Args for ``company.update``."""

    updates: dict[str, object] = Field(description="Fields to update")


class CompanyListDepartmentsArgs(_ArgsBase):
    """Args for ``company.list_departments``: no fields."""


class CompanyReorderDepartmentsArgs(_ArgsBase):
    """Args for ``company.reorder_departments``."""

    order: tuple[NotBlankStr, ...] = Field(description="Department names in order")


# ── Company versions ───────────────────────────────────────────────


class CompanyVersionsListArgs(PaginationFields):
    """Args for ``company_versions.list``."""


class CompanyVersionsGetArgs(_ArgsBase):
    """Args for ``company_versions.get``."""

    version_num: int = Field(ge=1, description="Version number")


# ── Departments ────────────────────────────────────────────────────


class DepartmentsListArgs(PaginationFields):
    """Args for ``departments.list``."""


class _DepartmentNameArgs(_ArgsBase):
    """Mixin for tools keyed by department ``name``."""

    name: NotBlankStr = Field(description="Department name")


class DepartmentsGetArgs(_DepartmentNameArgs):
    """Args for ``departments.get``."""


class DepartmentsCreateArgs(_DepartmentNameArgs):
    """Args for ``departments.create``."""

    description: str = Field(default="", description="Department description")


class DepartmentsUpdateArgs(_DepartmentNameArgs):
    """Args for ``departments.update``."""

    updates: dict[str, object] = Field(description="Fields to update")


class DepartmentsDeleteArgs(_DepartmentNameArgs):
    """Args for ``departments.delete``.

    The legacy schema didn't include destructive guardrails on this
    endpoint; we mirror that to avoid changing the wire contract.
    Operators that want guardrails should call the higher-level
    ``admin_tool`` flow instead.
    """


class DepartmentsGetHealthArgs(_DepartmentNameArgs):
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
    department: NotBlankStr = Field(description="Parent department")


class TeamsUpdateArgs(_ArgsBase):
    """Args for ``teams.update``."""

    team_id: NotBlankStr = Field(description="Team UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class TeamsDeleteArgs(_ArgsBase):
    """Args for ``teams.delete``.

    Mirrors :class:`DepartmentsDeleteArgs`: this endpoint is registered
    via ``write_tool`` (not ``admin_tool``) in ``organization.py`` to
    preserve the legacy wire contract that did not require destructive
    guardrails on team deletion.  Promoting it to ``admin_tool`` with
    ``AdminGuardrailFields`` would be a wire-breaking change and
    is tracked separately.
    """

    team_id: NotBlankStr = Field(description="Team UUID")


# ── Role versions ──────────────────────────────────────────────────


class RoleVersionsListArgs(PaginationFields):
    """Args for ``role_versions.list``."""

    role_name: NotBlankStr = Field(description="Role name")


class RoleVersionsGetArgs(_ArgsBase):
    """Args for ``role_versions.get``."""

    role_name: NotBlankStr = Field(description="Role name")
    version_num: int = Field(ge=1, description="Version number")


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
