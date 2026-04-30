"""Self-editing memory injection strategy.

Provides ``SelfEditingMemoryStrategy`` (Strategy 3 from design spec §7.7).
Agents maintain structured core/archival/recall memory blocks and
read/write them via six tools during execution.

Three memory tiers:

- **Core** (SEMANTIC + ``core`` tag): Always injected into context as a
  SYSTEM message.  Agents read/write via ``core_memory_read`` and
  ``core_memory_write``.
- **Archival** (any non-WORKING category): Never auto-injected; agents
  search on demand via ``archival_memory_search`` /
  ``archival_memory_write``.
- **Recall** (EPISODIC): Point-in-time lookup by ID via
  ``recall_memory_read`` / ``recall_memory_write``.
"""

import builtins
import copy
from types import MappingProxyType
from typing import Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.formatter import format_memory_context
from synthorg.memory.injection import (
    DefaultTokenEstimator,
    InjectionStrategy,
    TokenEstimator,
)
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.ranking import ScoredMemory
from synthorg.memory.self_editing_args import (
    ArchivalMemorySearchArgs,
    ArchivalMemoryWriteArgs,
    CoreMemoryReadArgs,
    CoreMemoryWriteArgs,
    RecallMemoryReadArgs,
    RecallMemoryWriteArgs,
    parse_self_editing_args,
)
from synthorg.memory.tool_retriever import ERROR_PREFIX
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_SELF_EDIT_ARCHIVAL_SEARCH,
    MEMORY_SELF_EDIT_ARCHIVAL_WRITE,
    MEMORY_SELF_EDIT_CORE_READ,
    MEMORY_SELF_EDIT_CORE_WRITE,
    MEMORY_SELF_EDIT_CORE_WRITE_REJECTED,
    MEMORY_SELF_EDIT_RECALL_READ,
    MEMORY_SELF_EDIT_RECALL_WRITE,
    MEMORY_SELF_EDIT_TOOL_EXECUTE,
    MEMORY_SELF_EDIT_TOOL_FAILED,
)
from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tool name constants
# ---------------------------------------------------------------------------

CORE_MEMORY_READ_TOOL: Final[str] = "core_memory_read"
CORE_MEMORY_WRITE_TOOL: Final[str] = "core_memory_write"
ARCHIVAL_MEMORY_SEARCH_TOOL: Final[str] = "archival_memory_search"
ARCHIVAL_MEMORY_WRITE_TOOL: Final[str] = "archival_memory_write"
RECALL_MEMORY_READ_TOOL: Final[str] = "recall_memory_read"
RECALL_MEMORY_WRITE_TOOL: Final[str] = "recall_memory_write"

# Auto-tag added to archival/recall writes when write_auto_tag=True.
_AUTO_TAG: Final[str] = "self_edited"

# ---------------------------------------------------------------------------
# JSON Schema constants (MappingProxyType -- read-only at module level)
# ---------------------------------------------------------------------------

_CORE_MEMORY_READ_SCHEMA: Final[MappingProxyType[str, Any]] = MappingProxyType(
    {
        "type": "object",
        "properties": {},
    }
)

_CORE_MEMORY_WRITE_SCHEMA: Final[MappingProxyType[str, Any]] = MappingProxyType(
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

_ARCHIVAL_MEMORY_SEARCH_SCHEMA: Final[MappingProxyType[str, Any]] = MappingProxyType(
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

_ARCHIVAL_MEMORY_WRITE_SCHEMA: Final[MappingProxyType[str, Any]] = MappingProxyType(
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

_RECALL_MEMORY_READ_SCHEMA: Final[MappingProxyType[str, Any]] = MappingProxyType(
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

_RECALL_MEMORY_WRITE_SCHEMA: Final[MappingProxyType[str, Any]] = MappingProxyType(
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

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_self_editing_error(err: object) -> str:
    """Render a single Pydantic ``errors()`` entry as ``loc: msg``.

    Strips the ``tool`` discriminator from ``loc`` (it's a dispatch
    concern not surfaced to the LLM caller).
    """
    if not isinstance(err, dict):
        return "<arguments>: invalid"
    loc_raw = err.get("loc", ())
    loc_parts = loc_raw if isinstance(loc_raw, tuple) else ()
    loc = ".".join(str(p) for p in loc_parts if p != "tool") or "<arguments>"
    msg = err.get("msg", "")
    return f"{loc}: {msg}" if isinstance(msg, str) else f"{loc}: invalid"


def _format_entries(entries: tuple[MemoryEntry, ...]) -> str:
    """Format memory entries as human-readable tool response text.

    Args:
        entries: Memory entries to format.

    Returns:
        Formatted multi-line string, or ``"No memories found."`` if empty.
    """
    if not entries:
        return "No memories found."
    return "\n".join(f"[{e.category.value}] (id={e.id}) {e.content}" for e in entries)


# ---------------------------------------------------------------------------
# SelfEditingMemoryConfig
# ---------------------------------------------------------------------------


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
        """WORKING is session-scoped -- disallow in persistent writes."""
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
        """
        if not self.archival_categories:
            msg = (
                "archival_categories must not be empty -- "
                "an empty set prevents all archival memory writes."
            )
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# SelfEditingMemoryStrategy
# ---------------------------------------------------------------------------


class SelfEditingMemoryStrategy:
    """Self-editing memory injection -- structured read/write memory blocks.

    Implements the ``MemoryInjectionStrategy`` protocol.  Core memory is
    injected as a SYSTEM message on every turn; archival and recall
    memory are accessed on-demand via agent tool calls.

    Args:
        backend: Connected memory backend (must satisfy
            ``MemoryBackend`` protocol).
        config: Strategy configuration.  Defaults to
            ``SelfEditingMemoryConfig()`` when ``None``.
        token_estimator: Token estimator for budget enforcement.
            Defaults to ``DefaultTokenEstimator()`` when ``None``.

    Raises:
        TypeError: When ``backend`` is ``None`` or does not satisfy the
            ``MemoryBackend`` protocol.
    """

    __slots__ = ("_backend", "_config", "_token_estimator")

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        config: SelfEditingMemoryConfig | None = None,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        _unchecked: object = backend
        if not isinstance(_unchecked, MemoryBackend):
            msg = (
                "backend must satisfy the MemoryBackend protocol, "
                f"got {type(_unchecked)!r}"
            )
            raise TypeError(msg)
        self._backend: MemoryBackend = backend
        self._config: SelfEditingMemoryConfig = (
            config if config is not None else SelfEditingMemoryConfig()
        )
        self._token_estimator: TokenEstimator = (
            token_estimator if token_estimator is not None else DefaultTokenEstimator()
        )

    @property
    def strategy_name(self) -> str:
        """Strategy identifier -- ``"self_editing"``."""
        return InjectionStrategy.SELF_EDITING.value

    def _core_query(self) -> MemoryQuery:
        """Return the MemoryQuery for core memory (SEMANTIC + core tag, no text)."""
        return MemoryQuery(
            text=None,
            categories=frozenset({MemoryCategory.SEMANTIC}),
            tags=(self._config.core_memory_tag,),
            limit=self._config.core_max_entries,
        )

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        query_text: NotBlankStr,  # noqa: ARG002
        token_budget: int,
    ) -> tuple[ChatMessage, ...]:
        """Return the core memory block as a SYSTEM message.

        Fetches SEMANTIC entries tagged with ``core_memory_tag`` and
        formats them within the token budget.  Returns ``()`` on
        backend error (fails open -- missing core memory is not a
        crash condition).

        Args:
            agent_id: Agent requesting memories.
            query_text: Ignored -- core memory is tag-filtered, not
                semantic.
            token_budget: Maximum tokens for the core memory block.

        Returns:
            Tuple with a single SYSTEM ``ChatMessage``, or ``()`` if
            the core is empty, the budget is zero, or the backend is
            unavailable.
        """
        try:
            entries = await self._backend.retrieve(agent_id, self._core_query())
            if not entries:
                return ()
            scored = tuple(
                ScoredMemory(
                    entry=e,
                    relevance_score=1.0,
                    recency_score=1.0,
                    combined_score=1.0,
                )
                for e in entries
            )
            return format_memory_context(
                scored,
                estimator=self._token_estimator,
                token_budget=token_budget,
            )
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MEMORY_SELF_EDIT_CORE_READ,
                source="prepare_messages",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return six tool definitions for the self-editing strategy.

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
                parameters_schema=copy.deepcopy(dict(_CORE_MEMORY_READ_SCHEMA)),
            ),
            ToolDefinition(
                name=CORE_MEMORY_WRITE_TOOL,
                description=(
                    "Append an entry to core memory. Core memory persists "
                    "across sessions and is always injected into context."
                ),
                parameters_schema=copy.deepcopy(dict(_CORE_MEMORY_WRITE_SCHEMA)),
            ),
            ToolDefinition(
                name=ARCHIVAL_MEMORY_SEARCH_TOOL,
                description=(
                    "Search archival memory by natural language query. "
                    "Archival memory is never auto-injected; use this tool "
                    "to retrieve relevant past context on demand."
                ),
                parameters_schema=copy.deepcopy(dict(_ARCHIVAL_MEMORY_SEARCH_SCHEMA)),
            ),
            ToolDefinition(
                name=ARCHIVAL_MEMORY_WRITE_TOOL,
                description=(
                    "Store a new entry in archival memory. Use for facts, "
                    "decisions, or events to retain for future retrieval."
                ),
                parameters_schema=copy.deepcopy(dict(_ARCHIVAL_MEMORY_WRITE_SCHEMA)),
            ),
            ToolDefinition(
                name=RECALL_MEMORY_READ_TOOL,
                description=(
                    "Retrieve a specific episodic memory by its ID. "
                    "Use the ID returned by recall_memory_write."
                ),
                parameters_schema=copy.deepcopy(dict(_RECALL_MEMORY_READ_SCHEMA)),
            ),
            ToolDefinition(
                name=RECALL_MEMORY_WRITE_TOOL,
                description=(
                    "Record an episodic event or experience. Returns the "
                    "memory ID for future retrieval via recall_memory_read."
                ),
                parameters_schema=copy.deepcopy(dict(_RECALL_MEMORY_WRITE_SCHEMA)),
            ),
        )

    async def handle_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        agent_id: NotBlankStr,
    ) -> str:
        """Dispatch a tool call to the appropriate handler.

        Validates the LLM-supplied ``arguments`` dict against the
        :class:`~synthorg.memory.self_editing_args.SelfEditingArgs`
        discriminated union before dispatch; missing keys, blank
        identifiers, oversized content, and unknown enum members all
        raise ``ValidationError`` and surface as an ``ERROR_PREFIX``
        response without ever reaching a handler.

        Args:
            tool_name: Name of the self-editing tool being called.
            arguments: Tool arguments from the LLM.
            agent_id: Calling agent identifier.

        Returns:
            String result for the LLM.  Errors start with
            ``ERROR_PREFIX`` (``"Error:"``).
        """
        logger.debug(
            MEMORY_SELF_EDIT_TOOL_EXECUTE,
            tool_name=tool_name,
            agent_id=agent_id,
        )
        # Defensive guard: ``parse_self_editing_args`` spreads
        # ``arguments`` into a dict before passing to ``TypeAdapter``,
        # which raises ``TypeError`` on non-mapping inputs (None, [],
        # scalars).  The static type says ``dict`` but the actual
        # LLM-provider call site is dynamic; catch the bad shape
        # upfront so the LLM-facing response stays in the
        # ``Error: ...`` envelope instead of bubbling out as an
        # internal failure.  Widen to ``object`` for the isinstance
        # check so mypy doesn't flag the runtime guard as unreachable.
        raw_arguments: object = arguments
        if not isinstance(raw_arguments, dict):
            return (
                f"{ERROR_PREFIX} Invalid arguments: "
                "<arguments>: input should be an object"
            )
        try:
            args = parse_self_editing_args(tool_name, arguments)
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_url=False)
            if not errors:
                return f"{ERROR_PREFIX} Invalid arguments."
            first = errors[0]
            if first["type"] == "union_tag_invalid":
                return f"{ERROR_PREFIX} Unknown self-editing tool: {tool_name!r}"
            details = "; ".join(_format_self_editing_error(e) for e in errors)
            return f"{ERROR_PREFIX} Invalid arguments: {details}"

        try:
            return await self._dispatch_tool_call(args, agent_id)
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Generic dispatch failure event: covers every self-editing
            # tool (read / write / search / recall) so a failed
            # core_memory_read is not recorded under the write-specific
            # MEMORY_SELF_EDIT_WRITE_FAILED constant.
            logger.warning(
                MEMORY_SELF_EDIT_TOOL_FAILED,
                tool_name=tool_name,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return f"{ERROR_PREFIX} Memory operation failed."

    async def _dispatch_tool_call(
        self,
        args: (
            CoreMemoryReadArgs
            | CoreMemoryWriteArgs
            | ArchivalMemorySearchArgs
            | ArchivalMemoryWriteArgs
            | RecallMemoryReadArgs
            | RecallMemoryWriteArgs
        ),
        agent_id: NotBlankStr,
    ) -> str:
        """Route a typed args variant to the corresponding private handler.

        Args:
            args: Typed self-editing-tool arguments.
            agent_id: Calling agent identifier.

        Returns:
            String result for the LLM.
        """
        match args:
            case CoreMemoryReadArgs():
                return await self._handle_core_memory_read(agent_id)
            case CoreMemoryWriteArgs():
                return await self._handle_core_memory_write(agent_id, args)
            case ArchivalMemorySearchArgs():
                return await self._handle_archival_memory_search(agent_id, args)
            case ArchivalMemoryWriteArgs():
                return await self._handle_archival_memory_write(agent_id, args)
            case RecallMemoryReadArgs():
                return await self._handle_recall_memory_read(agent_id, args)
            case RecallMemoryWriteArgs():
                return await self._handle_recall_memory_write(agent_id, args)

    # ------------------------------------------------------------------
    # Private handlers
    # ------------------------------------------------------------------

    async def _handle_core_memory_read(self, agent_id: NotBlankStr) -> str:
        """Read all core memory entries."""
        entries = await self._backend.retrieve(agent_id, self._core_query())
        logger.info(
            MEMORY_SELF_EDIT_CORE_READ,
            agent_id=agent_id,
            count=len(entries),
        )
        return _format_entries(entries)

    async def _handle_core_memory_write(
        self,
        agent_id: NotBlankStr,
        args: CoreMemoryWriteArgs,
    ) -> str:
        """Append an entry to core memory.

        Note: The capacity check (retrieve then store) is advisory -- it is
        not atomic. Concurrent writes from the same agent may both pass the
        count check and both succeed, temporarily exceeding ``core_max_entries``
        until the next write is rejected. This is acceptable for a
        best-effort memory cap.
        """
        if not self._config.allow_core_writes:
            logger.info(
                MEMORY_SELF_EDIT_CORE_WRITE_REJECTED,
                agent_id=agent_id,
                reason="allow_core_writes=False",
            )
            return f"{ERROR_PREFIX} Core memory writes are disabled for this agent."

        content = args.content
        existing = await self._backend.retrieve(agent_id, self._core_query())
        if len(existing) >= self._config.core_max_entries:
            logger.info(
                MEMORY_SELF_EDIT_CORE_WRITE_REJECTED,
                agent_id=agent_id,
                reason="max_entries_exceeded",
                count=len(existing),
                max_entries=self._config.core_max_entries,
            )
            return (
                f"{ERROR_PREFIX} Core memory is full "
                f"({self._config.core_max_entries} entries). "
                "Delete or edit an existing entry first."
            )

        request = MemoryStoreRequest(
            category=MemoryCategory.SEMANTIC,
            content=content,
            metadata=MemoryMetadata(tags=(self._config.core_memory_tag,)),
        )
        memory_id = await self._backend.store(agent_id, request)
        logger.info(
            MEMORY_SELF_EDIT_CORE_WRITE,
            agent_id=agent_id,
            memory_id=memory_id,
        )
        return f"Core memory stored (id={memory_id})."

    async def _handle_archival_memory_search(
        self,
        agent_id: NotBlankStr,
        args: ArchivalMemorySearchArgs,
    ) -> str:
        """Search archival memory by natural language query."""
        categories: frozenset[MemoryCategory] | None = (
            frozenset({args.category}) if args.category is not None else None
        )

        # Apply the config-driven cap.  Pydantic validated ``limit`` is
        # positive (or ``None``); the runtime clamp keeps it in
        # ``[1, archival_search_limit]``.
        cap = self._config.archival_search_limit
        limit = min(args.limit, cap) if args.limit is not None else cap

        entries = await self._backend.retrieve(
            agent_id,
            MemoryQuery(
                text=args.query,
                categories=categories,
                limit=limit,
            ),
        )
        logger.info(
            MEMORY_SELF_EDIT_ARCHIVAL_SEARCH,
            agent_id=agent_id,
            query=args.query,
            count=len(entries),
        )
        return _format_entries(entries)

    async def _handle_archival_memory_write(
        self,
        agent_id: NotBlankStr,
        args: ArchivalMemoryWriteArgs,
    ) -> str:
        """Store an entry in archival memory."""
        category = args.category
        if category not in self._config.archival_categories:
            valid = ", ".join(sorted(c.value for c in self._config.archival_categories))
            return (
                f"{ERROR_PREFIX} Category {category.value!r} cannot be "
                "written to archival memory. "
                f"Valid values: {valid}."
            )

        tags: tuple[str, ...] = (_AUTO_TAG,) if self._config.write_auto_tag else ()
        request = MemoryStoreRequest(
            category=category,
            content=args.content,
            metadata=MemoryMetadata(tags=tags),
        )
        memory_id = await self._backend.store(agent_id, request)
        logger.info(
            MEMORY_SELF_EDIT_ARCHIVAL_WRITE,
            agent_id=agent_id,
            category=category.value,
            memory_id=memory_id,
        )
        return f"Archival memory stored (id={memory_id}, category={category.value})."

    async def _handle_recall_memory_read(
        self,
        agent_id: NotBlankStr,
        args: RecallMemoryReadArgs,
    ) -> str:
        """Retrieve a specific episodic memory by ID."""
        entry = await self._backend.get(agent_id, args.memory_id)
        logger.info(
            MEMORY_SELF_EDIT_RECALL_READ,
            agent_id=agent_id,
            memory_id=args.memory_id,
            found=entry is not None,
        )
        if entry is None:
            return f"{ERROR_PREFIX} Memory not found: {args.memory_id!r}"
        return f"[{entry.category.value}] {entry.content}"

    async def _handle_recall_memory_write(
        self,
        agent_id: NotBlankStr,
        args: RecallMemoryWriteArgs,
    ) -> str:
        """Record an episodic event or experience."""
        tags: tuple[str, ...] = (_AUTO_TAG,) if self._config.write_auto_tag else ()
        request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content=args.content,
            metadata=MemoryMetadata(tags=tags),
        )
        memory_id = await self._backend.store(agent_id, request)
        logger.info(
            MEMORY_SELF_EDIT_RECALL_WRITE,
            agent_id=agent_id,
            memory_id=memory_id,
        )
        return f"Episodic memory recorded (id={memory_id})."
