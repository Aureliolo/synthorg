"""Long-horizon project-brain domain MCP tools.

Operator-driven MCP tools mirroring the agent-tool surface in
:mod:`synthorg.tools.brain`. Get / list / query / history are read-only;
append / resolve / supersede / clear-blocker are admin (their handlers call
``require_admin_guardrails`` and their args carry the ``confirm`` + ``reason``
guardrail fields).
"""

from typing import TYPE_CHECKING

from pydantic import JsonValue

from synthorg.meta.mcp.domains._brain_args import (
    BrainAppendArgs,
    BrainClearBlockerArgs,
    BrainGetArgs,
    BrainHistoryArgs,
    BrainListArgs,
    BrainQueryArgs,
    BrainResolveArgs,
    BrainSupersedeArgs,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    admin_tool,
    read_tool,
)
from synthorg.project_brain.constants import (
    BRAIN_SEARCH_MAX_LIMIT,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

_KIND_VALUES = [
    "decision",
    "open_question",
    "blocker",
    "risk",
    "dependency",
    "plan_revision",
]
_STATUS_VALUES = [
    "open",
    "resolved",
    "accepted",
    "superseded",
    "blocked",
    "cleared",
    "active",
    "mitigated",
    "retired",
]

_PAYLOAD_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "description": (
        "Kind-specific payload, discriminated on entry_kind (decision / "
        "open_question / blocker / risk / dependency / plan_revision). The "
        "service validates the typed shape downstream; an unknown entry_kind "
        "or a payload mismatching the kind is rejected."
    ),
    "additionalProperties": True,
}


BRAIN_TOOLS: tuple[MCPToolDef, ...] = (
    admin_tool(
        "brain",
        "append",
        "Record a new project-brain entry, or revise an existing one by "
        "passing its entry_id. Each change is a new revision.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "author": {"type": "string", "minLength": 1},
            "entry_id": {
                "type": ["string", "null"],
                "description": "Existing entry to revise; null to create",
                "minLength": 1,
            },
            "title": {"type": ["string", "null"], "minLength": 1},
            "rationale": {"type": ["string", "null"], "minLength": 1},
            "status": {"type": ["string", "null"], "enum": [*_STATUS_VALUES, None]},
            "payload": _PAYLOAD_SCHEMA,
            "related_task_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "related_entry_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "supersedes_entry_id": {"type": ["string", "null"], "minLength": 1},
            "tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "confidence": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
            "citations": {"type": "array", "items": {"type": "object"}},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("project_id", "author", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=BrainAppendArgs,
    ),
    admin_tool(
        "brain",
        "resolve",
        "Resolve an open question (recording an optional answer) or a dependency.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "entry_id": {"type": "string", "minLength": 1},
            "author": {"type": "string", "minLength": 1},
            "answer": {"type": ["string", "null"], "minLength": 1},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("project_id", "entry_id", "author", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=BrainResolveArgs,
    ),
    admin_tool(
        "brain",
        "supersede",
        "Mark a decision or plan revision superseded and link the successor.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "entry_id": {"type": "string", "minLength": 1},
            "by_entry_id": {"type": "string", "minLength": 1},
            "author": {"type": "string", "minLength": 1},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=(
            "project_id",
            "entry_id",
            "by_entry_id",
            "author",
            *ADMIN_GUARDRAIL_REQUIRED,
        ),
        args_model=BrainSupersedeArgs,
    ),
    admin_tool(
        "brain",
        "clear_blocker",
        "Clear a blocker, recording how it was resolved.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "entry_id": {"type": "string", "minLength": 1},
            "author": {"type": "string", "minLength": 1},
            "resolution": {"type": ["string", "null"], "minLength": 1},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("project_id", "entry_id", "author", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=BrainClearBlockerArgs,
    ),
    read_tool(
        "brain",
        "get",
        "Read one brain entry, latest or at an exact revision.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "entry_id": {"type": "string", "minLength": 1},
            "revision": {"type": ["integer", "null"], "minimum": 1},
        },
        required=("project_id", "entry_id"),
        args_model=BrainGetArgs,
    ),
    read_tool(
        "brain",
        "list",
        "List the current-state projection for a project, filtered by kind "
        "and status, newest-first.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "entry_kind": {"type": ["string", "null"], "enum": [*_KIND_VALUES, None]},
            "status": {"type": ["string", "null"], "enum": [*_STATUS_VALUES, None]},
            "tag": {"type": ["string", "null"], "minLength": 1},
            "author": {"type": ["string", "null"], "minLength": 1},
            "related_task_id": {"type": ["string", "null"], "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0},
        },
        required=("project_id",),
        args_model=BrainListArgs,
    ),
    read_tool(
        "brain",
        "query",
        "Semantic search across a project's indexed brain entries.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "query": {"type": "string", "minLength": 1},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": BRAIN_SEARCH_MAX_LIMIT,
            },
        },
        required=("project_id", "query"),
        args_model=BrainQueryArgs,
    ),
    read_tool(
        "brain",
        "history",
        "Return the full structured revision chain of one brain entry, oldest-first.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "entry_id": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        required=("project_id", "entry_id"),
        args_model=BrainHistoryArgs,
    ),
)
