"""Knowledge-substrate domain MCP tools.

Operator-driven MCP tools mirroring the agent-tool surface in
:mod:`synthorg.tools.knowledge`. Search / list / get are read-only;
ingest / reindex / delete are admin (their handlers call
``require_admin_guardrails``).
"""

from typing import TYPE_CHECKING

from pydantic import JsonValue

from synthorg.knowledge.constants import (
    KNOWLEDGE_LIST_MAX_LIMIT,
    KNOWLEDGE_SEARCH_MAX_LIMIT,
)
from synthorg.meta.mcp.domains._knowledge_args import (
    KnowledgeDeleteArgs,
    KnowledgeGetArgs,
    KnowledgeIngestArgs,
    KnowledgeListArgs,
    KnowledgeReindexArgs,
    KnowledgeSearchArgs,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    admin_tool,
    read_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

_SOURCE_TYPES: list[JsonValue] = ["pdf", "web", "repo", "ticket", "design_doc"]


KNOWLEDGE_TOOLS: tuple[MCPToolDef, ...] = (
    read_tool(
        "knowledge",
        "search",
        "Search the ingested knowledge corpus; returns cited hits that "
        "resolve to the exact source chunk.",
        {
            "project_id": {
                "type": ["string", "null"],
                "description": "Scope to a project; null searches global only",
                "minLength": 1,
            },
            "query": {"type": "string", "minLength": 1},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": KNOWLEDGE_SEARCH_MAX_LIMIT,
            },
        },
        required=("query",),
        args_model=KnowledgeSearchArgs,
    ),
    admin_tool(
        "knowledge",
        "ingest",
        "Ingest a source (PDF / web / repo) into the corpus; re-ingesting "
        "re-indexes only changed chunks.",
        {
            "project_id": {
                "type": ["string", "null"],
                "description": "Owning project; null ingests a global source",
                "minLength": 1,
            },
            "source_type": {"type": "string", "enum": _SOURCE_TYPES},
            "uri": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("source_type", "uri", "title", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=KnowledgeIngestArgs,
    ),
    admin_tool(
        "knowledge",
        "reindex",
        "Force a re-load + re-index of an existing source.",
        {
            "source_id": {"type": "string", "minLength": 1},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("source_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=KnowledgeReindexArgs,
    ),
    read_tool(
        "knowledge",
        "list",
        "List registered knowledge sources, recency-first.",
        {
            "project_id": {"type": ["string", "null"], "minLength": 1},
            "include_global": {"type": "boolean"},
            "stale_only": {"type": "boolean"},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": KNOWLEDGE_LIST_MAX_LIMIT,
            },
            "offset": {"type": "integer", "minimum": 0},
        },
        args_model=KnowledgeListArgs,
    ),
    read_tool(
        "knowledge",
        "get",
        "Read a single knowledge source by id.",
        {"source_id": {"type": "string", "minLength": 1}},
        required=("source_id",),
        args_model=KnowledgeGetArgs,
    ),
    admin_tool(
        "knowledge",
        "delete",
        "Delete a source and purge its corpus entries + provenance.",
        {
            "source_id": {"type": "string", "minLength": 1},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("source_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=KnowledgeDeleteArgs,
    ),
)
