"""Models and tool-parameter schemas for self-editing memory.

Holds the six tool-name constants, their JSON Schema parameter
definitions, the ``build_self_editing_tool_definitions`` factory, and
the ``SelfEditingMemoryConfig`` model.
"""

import copy
from types import MappingProxyType
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.providers.models import ToolDefinition

# Tool name constants -- the six self-editing tools.
CORE_MEMORY_READ_TOOL: Final[str] = "core_memory_read"
CORE_MEMORY_WRITE_TOOL: Final[str] = "core_memory_write"
ARCHIVAL_MEMORY_SEARCH_TOOL: Final[str] = "archival_memory_search"
ARCHIVAL_MEMORY_WRITE_TOOL: Final[str] = "archival_memory_write"
RECALL_MEMORY_READ_TOOL: Final[str] = "recall_memory_read"
RECALL_MEMORY_WRITE_TOOL: Final[str] = "recall_memory_write"

# Read-only JSON Schema mapping (tool parameter schemas).
type _SchemaDict = MappingProxyType[str, JsonValue]

CORE_MEMORY_READ_SCHEMA: Final[_SchemaDict] = MappingProxyType(
    {
        "type": "object",
        "properties": {},
    }
)

CORE_MEMORY_WRITE_SCHEMA: Final[_SchemaDict] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Text to store in core memory.",
            },
        },
        "required": ["content"],
    }
)

ARCHIVAL_MEMORY_SEARCH_SCHEMA: Final[_SchemaDict] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional category filter (episodic, semantic, procedural, social)."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return.",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    }
)

ARCHIVAL_MEMORY_WRITE_SCHEMA: Final[_SchemaDict] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Text to store in archival memory.",
            },
            "category": {
                "type": "string",
                "description": (
                    "Memory category (episodic, semantic, procedural, social)."
                ),
            },
        },
        "required": ["content", "category"],
    }
)

RECALL_MEMORY_READ_SCHEMA: Final[_SchemaDict] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "Exact memory ID to retrieve.",
            },
        },
        "required": ["memory_id"],
    }
)

RECALL_MEMORY_WRITE_SCHEMA: Final[_SchemaDict] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Episodic event or experience to record.",
            },
        },
        "required": ["content"],
    }
)


class SelfEditingMemoryConfig(BaseModel):
    """Configuration for ``SelfEditingMemoryStrategy``.

    Attributes:
        core_memory_token_budget: Token budget for the core memory
            context block (256-8192).
        core_memory_tag: Tag used to identify core memory entries.
        allow_core_writes: When ``False``, ``core_memory_write`` is
            rejected for this agent (read-only core).
        core_max_entries: Maximum core entries before writes are
            rejected (1-200).
        archival_search_limit: Maximum results returned by
            ``archival_memory_search`` (1-50).
        archival_categories: Categories allowed in archival memory.
            ``WORKING`` is always excluded and the set must not be
            empty (both enforced by validators).
        write_auto_tag: When ``True``, automatically adds the
            ``"self_edited"`` tag to archival and recall writes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    core_memory_token_budget: int = Field(
        default=1024,
        ge=256,
        le=8192,
        description="Token budget for the core memory context block.",
    )
    core_memory_tag: NotBlankStr = Field(
        default="core",
        description="Tag used to identify core memory entries.",
    )
    allow_core_writes: bool = Field(
        default=True,
        description=(
            "When False, core_memory_write is rejected (read-only core memory)."
        ),
    )
    core_max_entries: int = Field(
        default=20,
        ge=1,
        le=200,
        description=("Maximum core memory entries before writes are rejected."),
    )
    archival_search_limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description=("Maximum results returned by archival_memory_search."),
    )
    archival_categories: frozenset[MemoryCategory] = Field(
        default_factory=lambda: frozenset(
            {
                MemoryCategory.EPISODIC,
                MemoryCategory.SEMANTIC,
                MemoryCategory.PROCEDURAL,
                MemoryCategory.SOCIAL,
            }
        ),
        description=(
            "Categories allowed in archival memory. WORKING is always excluded."
        ),
    )
    write_auto_tag: bool = Field(
        default=True,
        description=(
            "When True, automatically adds 'self_edited' tag to "
            "archival and recall writes."
        ),
    )

    @model_validator(mode="after")
    def _no_working_in_archival(self) -> Self:
        """WORKING is session-scoped -- disallow in persistent writes.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if MemoryCategory.WORKING in self.archival_categories:
            msg = (
                "MemoryCategory.WORKING must not appear in "
                "archival_categories -- WORKING is session-scoped "
                "and must not be persisted via self-editing tools."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _archival_categories_not_empty(self) -> Self:
        """archival_categories must not be empty.

        An empty set prevents all archival memory writes.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not self.archival_categories:
            msg = (
                "archival_categories must not be empty -- "
                "an empty set prevents all archival memory writes."
            )
            raise ValueError(msg)
        return self


def build_self_editing_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Build the six tool definitions for the self-editing strategy.

    Each schema is deep-copied into a mutable ``dict`` so callers receive
    an independent, mutable parameters schema rather than a shared
    read-only ``MappingProxyType``.

    Returns:
        Tuple of six ``ToolDefinition`` instances (core read/write,
        archival search/write, recall read/write).
    """
    return (
        ToolDefinition(
            name=CORE_MEMORY_READ_TOOL,
            description=(
                "Read the current core memory block (persona, goals, "
                "key knowledge stored as SEMANTIC memories)."
            ),
            parameters_schema=copy.deepcopy(dict(CORE_MEMORY_READ_SCHEMA)),
        ),
        ToolDefinition(
            name=CORE_MEMORY_WRITE_TOOL,
            description=(
                "Append an entry to core memory. Core memory persists "
                "across sessions and is always injected into context."
            ),
            parameters_schema=copy.deepcopy(dict(CORE_MEMORY_WRITE_SCHEMA)),
        ),
        ToolDefinition(
            name=ARCHIVAL_MEMORY_SEARCH_TOOL,
            description=(
                "Search archival memory by natural language query. "
                "Archival memory is never auto-injected; use this tool "
                "to retrieve relevant past context on demand."
            ),
            parameters_schema=copy.deepcopy(dict(ARCHIVAL_MEMORY_SEARCH_SCHEMA)),
        ),
        ToolDefinition(
            name=ARCHIVAL_MEMORY_WRITE_TOOL,
            description=(
                "Store a new entry in archival memory. Use for facts, "
                "decisions, or events to retain for future retrieval."
            ),
            parameters_schema=copy.deepcopy(dict(ARCHIVAL_MEMORY_WRITE_SCHEMA)),
        ),
        ToolDefinition(
            name=RECALL_MEMORY_READ_TOOL,
            description=(
                "Retrieve a specific episodic memory by its ID. "
                "Use the ID returned by recall_memory_write."
            ),
            parameters_schema=copy.deepcopy(dict(RECALL_MEMORY_READ_SCHEMA)),
        ),
        ToolDefinition(
            name=RECALL_MEMORY_WRITE_TOOL,
            description=(
                "Record an episodic event or experience. Returns the "
                "memory ID for future retrieval via recall_memory_read."
            ),
            parameters_schema=copy.deepcopy(dict(RECALL_MEMORY_WRITE_SCHEMA)),
        ),
    )
