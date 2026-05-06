"""Workflow domain MCP tools.

Covers workflows, subworkflows, workflow executions, and workflow versions.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._workflows_org_args import (
    SubworkflowsCreateArgs,
    SubworkflowsDeleteArgs,
    SubworkflowsGetArgs,
    SubworkflowsListArgs,
    WorkflowExecutionsCancelArgs,
    WorkflowExecutionsGetArgs,
    WorkflowExecutionsListArgs,
    WorkflowExecutionsStartArgs,
    WorkflowsCreateArgs,
    WorkflowsDeleteArgs,
    WorkflowsGetArgs,
    WorkflowsListArgs,
    WorkflowsUpdateArgs,
    WorkflowsValidateArgs,
    WorkflowVersionsGetArgs,
    WorkflowVersionsListArgs,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
    write_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

WORKFLOW_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Workflow CRUD ---
    read_tool(
        "workflows",
        "list",
        "List workflow definitions.",
        PAGINATION_PROPERTIES,
        args_model=WorkflowsListArgs,
    ),
    read_tool(
        "workflows",
        "get",
        "Get a workflow definition by ID.",
        {
            "workflow_id": {"type": "string", "description": "Workflow UUID"},
        },
        required=("workflow_id",),
        args_model=WorkflowsGetArgs,
    ),
    write_tool(
        "workflows",
        "create",
        "Create a new workflow definition.",
        {
            "name": {"type": "string", "description": "Workflow name"},
            "steps": {"type": "array", "description": "Workflow step definitions"},
        },
        required=("name", "steps"),
        args_model=WorkflowsCreateArgs,
    ),
    write_tool(
        "workflows",
        "update",
        "Update a workflow definition.",
        {
            "workflow_id": {"type": "string", "description": "Workflow UUID"},
            "updates": {"type": "object", "description": "Fields to update"},
        },
        required=("workflow_id", "updates"),
        args_model=WorkflowsUpdateArgs,
    ),
    admin_tool(
        "workflows",
        "delete",
        "Delete a workflow definition (destructive; requires confirm).",
        {
            "workflow_id": {"type": "string", "description": "Workflow UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("workflow_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=WorkflowsDeleteArgs,
    ),
    read_tool(
        "workflows",
        "validate",
        "Validate a workflow definition.",
        {
            "workflow_id": {"type": "string", "description": "Workflow UUID"},
        },
        required=("workflow_id",),
        args_model=WorkflowsValidateArgs,
    ),
    # --- Subworkflows ---
    read_tool(
        "subworkflows",
        "list",
        "List subworkflows for a workflow.",
        {
            "workflow_id": {"type": "string", "description": "Parent workflow UUID"},
            **PAGINATION_PROPERTIES,
        },
        required=("workflow_id",),
        args_model=SubworkflowsListArgs,
    ),
    read_tool(
        "subworkflows",
        "get",
        "Get a subworkflow by ID.",
        {
            "subworkflow_id": {"type": "string", "description": "Subworkflow UUID"},
        },
        required=("subworkflow_id",),
        args_model=SubworkflowsGetArgs,
    ),
    write_tool(
        "subworkflows",
        "create",
        "Create a subworkflow.",
        {
            "workflow_id": {"type": "string", "description": "Parent workflow UUID"},
            "name": {"type": "string", "description": "Subworkflow name"},
            "steps": {"type": "array", "description": "Step definitions"},
        },
        required=("workflow_id", "name"),
        args_model=SubworkflowsCreateArgs,
    ),
    admin_tool(
        "subworkflows",
        "delete",
        "Delete a subworkflow (destructive; requires confirm).",
        {
            "subworkflow_id": {"type": "string", "description": "Subworkflow UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("subworkflow_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=SubworkflowsDeleteArgs,
    ),
    # --- Workflow executions ---
    read_tool(
        "workflow_executions",
        "list",
        "List workflow execution runs.",
        {
            "workflow_id": {"type": "string", "description": "Filter by workflow"},
            "status": {"type": "string", "description": "Filter by execution status"},
            **PAGINATION_PROPERTIES,
        },
        args_model=WorkflowExecutionsListArgs,
    ),
    read_tool(
        "workflow_executions",
        "get",
        "Get a workflow execution by ID.",
        {
            "execution_id": {"type": "string", "description": "Execution UUID"},
        },
        required=("execution_id",),
        args_model=WorkflowExecutionsGetArgs,
    ),
    write_tool(
        "workflow_executions",
        "start",
        "Start a workflow execution.",
        {
            "workflow_id": {"type": "string", "description": "Workflow to execute"},
            "parameters": {"type": "object", "description": "Execution parameters"},
        },
        required=("workflow_id",),
        args_model=WorkflowExecutionsStartArgs,
    ),
    admin_tool(
        "workflow_executions",
        "cancel",
        "Cancel a running workflow execution (destructive; requires confirm).",
        {
            "execution_id": {"type": "string", "description": "Execution UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("execution_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=WorkflowExecutionsCancelArgs,
    ),
    # --- Workflow versions ---
    read_tool(
        "workflow_versions",
        "list",
        "List versions of a workflow.",
        {
            "workflow_id": {"type": "string", "description": "Workflow UUID"},
            **PAGINATION_PROPERTIES,
        },
        required=("workflow_id",),
        args_model=WorkflowVersionsListArgs,
    ),
    read_tool(
        "workflow_versions",
        "get",
        "Get a specific workflow version.",
        {
            "workflow_id": {"type": "string", "description": "Workflow UUID"},
            "version_num": {
                "type": "integer",
                "description": "Version number",
                "minimum": 1,
            },
        },
        required=("workflow_id", "version_num"),
        args_model=WorkflowVersionsGetArgs,
    ),
)
