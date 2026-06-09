"""Living-documentation domain MCP tools.

Exposes operator-driven MCP tools that mirror the agent-tool surface
exposed by :mod:`synthorg.tools.docs`. The write tool is gated by the
admin capability; the read / list / search / history tools are
read-only and don't require admin scope.
"""

from typing import TYPE_CHECKING

from pydantic import JsonValue

from synthorg.meta.mcp.domains._docs_args import (
    DocsHistoryArgs,
    DocsListArgs,
    DocsReadArgs,
    DocsSearchArgs,
    DocsWriteArgs,
)
from synthorg.meta.mcp.tool_builder import admin_tool, read_tool

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef


_BLOCK_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "description": (
        "Doc body block (heading / prose / bullet_list / code / "
        "decision / metric / link). The service validates the typed "
        "shape downstream; unknown block_kind values are rejected."
    ),
    "additionalProperties": True,
}


DOCS_TOOLS: tuple[MCPToolDef, ...] = (
    admin_tool(
        "docs",
        "write",
        "Create or update a living document for a project.",
        {
            "project_id": {
                "type": "string",
                "description": "Owning project identifier",
                "minLength": 1,
            },
            "title": {
                "type": "string",
                "description": "Human-readable doc title",
                "minLength": 1,
            },
            "doc_type": {
                "type": "string",
                "enum": ["status_report", "deliverable", "knowledge_note"],
                "description": "Taxonomy bucket",
            },
            "author_agent_id": {
                "type": "string",
                "description": "Writer identifier (operator or agent id)",
                "minLength": 1,
            },
            "body": {
                "type": "array",
                "description": "Ordered list of typed body blocks",
                "minItems": 1,
                "items": _BLOCK_SCHEMA,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": "Free-form classification tags",
            },
            "related_task_ids": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": "Task IDs that produced or reference this doc",
            },
            "slug": {
                "type": ["string", "null"],
                "description": "Existing slug to update; null to create",
                "minLength": 1,
            },
        },
        required=("project_id", "title", "doc_type", "author_agent_id", "body"),
        args_model=DocsWriteArgs,
    ),
    read_tool(
        "docs",
        "get",
        "Read a living document by slug (optionally at a historical SHA).",
        {
            "project_id": {"type": "string", "minLength": 1},
            "slug": {"type": "string", "minLength": 1},
            "version": {
                "type": ["string", "null"],
                "description": "Optional commit SHA on synthorg/docs",
                "minLength": 1,
            },
        },
        required=("project_id", "slug"),
        args_model=DocsReadArgs,
    ),
    read_tool(
        "docs",
        "list",
        "List living docs for a project, recency-first.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "doc_type": {
                "type": ["string", "null"],
                "enum": [
                    "status_report",
                    "deliverable",
                    "knowledge_note",
                    None,
                ],
            },
            "tag": {"type": ["string", "null"], "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "offset": {"type": "integer", "minimum": 0},
        },
        required=("project_id",),
        args_model=DocsListArgs,
    ),
    read_tool(
        "docs",
        "search",
        "Semantic search across a project's indexed living docs.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "query": {"type": "string", "minLength": 1},
            "doc_types": {
                "type": ["array", "null"],
                "items": {
                    "type": "string",
                    "enum": [
                        "status_report",
                        "deliverable",
                        "knowledge_note",
                    ],
                },
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 64},
        },
        required=("project_id", "query"),
        args_model=DocsSearchArgs,
    ),
    read_tool(
        "docs",
        "history",
        "Return the git commit history for one living doc.",
        {
            "project_id": {"type": "string", "minLength": 1},
            "slug": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        required=("project_id", "slug"),
        args_model=DocsHistoryArgs,
    ),
)
