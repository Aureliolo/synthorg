"""Research-subsystem domain MCP tools.

Operator / agent-driven MCP tools mirroring the agent-tool surface in
:mod:`synthorg.research.tool`. ``run`` executes a research brief and
returns a cited report; ``get`` / ``list`` are read-only over the run
record.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._research_args import (
    ResearchGetArgs,
    ResearchListArgs,
    ResearchRunArgs,
)
from synthorg.meta.mcp.tool_builder import read_tool, write_tool
from synthorg.research.constants import RESEARCH_LIST_MAX_LIMIT
from synthorg.research.enums import ResearchRunStatus

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

_STATUSES: tuple[str, ...] = tuple(status.value for status in ResearchRunStatus)
"""Run-status filter values for ``research:list``, derived from the single
source of truth so the exposed schema cannot drift from validation."""


RESEARCH_TOOLS: tuple[MCPToolDef, ...] = (
    write_tool(
        "research",
        "run",
        "Run a research brief: plan queries, consult internal knowledge plus "
        "web / academic / code sources, triage credibility, and synthesise a "
        "citation-backed report whose claims resolve to retrievable sources.",
        {
            "project_id": {
                "type": ["string", "null"],
                "description": "Scope knowledge to a project; null = global",
                "minLength": 1,
            },
            "question": {"type": "string", "minLength": 1, "maxLength": 16384},
            "title": {"type": ["string", "null"], "minLength": 1},
            "include_knowledge": {"type": "boolean"},
            "include_web": {"type": "boolean"},
            "include_academic": {"type": "boolean"},
            "include_code": {"type": "boolean"},
            "min_credibility": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "max_subqueries": {"type": "integer", "minimum": 1},
        },
        required=("question",),
        args_model=ResearchRunArgs,
    ),
    read_tool(
        "research",
        "get",
        "Read a single research run (its report and provenance) by run id.",
        {"run_id": {"type": "string", "minLength": 1}},
        required=("run_id",),
        args_model=ResearchGetArgs,
    ),
    read_tool(
        "research",
        "list",
        "List research runs, recency-first, filtered by brief / project / status.",
        {
            "brief_id": {"type": ["string", "null"], "minLength": 1},
            "project_id": {"type": ["string", "null"], "minLength": 1},
            "status": {"type": ["string", "null"], "enum": [*_STATUSES, None]},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": RESEARCH_LIST_MAX_LIMIT,
            },
            "offset": {"type": "integer", "minimum": 0},
        },
        args_model=ResearchListArgs,
    ),
)
