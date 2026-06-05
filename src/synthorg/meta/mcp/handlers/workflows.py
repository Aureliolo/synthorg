"""Workflow domain MCP handlers.

16 tools spanning workflow definitions, subworkflows, executions, and
versions. The handler bodies live in sibling modules: definition CRUD in
``workflows_definitions``, subworkflows in ``workflows_subworkflows``,
version history in ``workflows_versions``, and executions in
``workflow_executions``. This module aggregates them into the read-only
``WORKFLOW_HANDLERS`` map.

Destructive ops -- ``workflows_delete``, ``subworkflows_delete``, and
``workflow_executions_cancel`` -- enforce the full guardrail
(``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor``) and
emit ``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_cancel as _workflow_executions_cancel,
)
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_get as _workflow_executions_get,
)
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_list as _workflow_executions_list,
)
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_start as _workflow_executions_start,
)
from synthorg.meta.mcp.handlers.workflows_definitions import (
    _workflows_create,
    _workflows_delete,
    _workflows_get,
    _workflows_list,
    _workflows_update,
    _workflows_validate,
)
from synthorg.meta.mcp.handlers.workflows_subworkflows import (
    _subworkflows_create,
    _subworkflows_delete,
    _subworkflows_get,
    _subworkflows_list,
)
from synthorg.meta.mcp.handlers.workflows_versions import (
    _workflow_versions_get,
    _workflow_versions_list,
)

WORKFLOW_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_workflows_list": _workflows_list,
        "synthorg_workflows_get": _workflows_get,
        "synthorg_workflows_create": _workflows_create,
        "synthorg_workflows_update": _workflows_update,
        "synthorg_workflows_delete": _workflows_delete,
        "synthorg_workflows_validate": _workflows_validate,
        "synthorg_subworkflows_list": _subworkflows_list,
        "synthorg_subworkflows_get": _subworkflows_get,
        "synthorg_subworkflows_create": _subworkflows_create,
        "synthorg_subworkflows_delete": _subworkflows_delete,
        "synthorg_workflow_executions_list": _workflow_executions_list,
        "synthorg_workflow_executions_get": _workflow_executions_get,
        "synthorg_workflow_executions_start": _workflow_executions_start,
        "synthorg_workflow_executions_cancel": _workflow_executions_cancel,
        "synthorg_workflow_versions_list": _workflow_versions_list,
        "synthorg_workflow_versions_get": _workflow_versions_get,
    },
)
