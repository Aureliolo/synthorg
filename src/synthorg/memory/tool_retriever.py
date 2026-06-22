"""Tool-based memory injection strategy.

Provides ``search_memory`` and ``recall_memory`` tool definitions
that agents invoke on-demand during execution.  Implements the
``MemoryInjectionStrategy`` protocol with tool-based retrieval.
"""

import asyncio
import builtins
import copy
from types import MappingProxyType
from typing import Final

from pydantic import JsonValue, ValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.errors import MemoryError as DomainMemoryError
from synthorg.memory.filter import MemoryFilterStrategy
from synthorg.memory.injection import TokenEstimator
from synthorg.memory.models import MemoryEntry
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.reformulation import (
    QueryReformulator,
    SufficiencyChecker,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.memory.tool_retriever_helpers import (
    _format_entries,
    _parse_search_args,
)
from synthorg.memory.tool_retriever_reformulation import ToolBasedReformulationMixin
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.memory import (
    MEMORY_RETRIEVAL_COMPLETE,
    MEMORY_RETRIEVAL_DEGRADED,
    MEMORY_RETRIEVAL_START,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)

SEARCH_MEMORY_TOOL_NAME = "search_memory"
RECALL_MEMORY_TOOL_NAME = "recall_memory"

# Error message constants.  ``memory/tools.py`` performs PREFIX matching
# on these exact strings to detect user-facing tool errors.  All error
# messages start with ``ERROR_PREFIX`` so the matcher can check via
# ``startswith(ERROR_PREFIX)`` rather than substring matching.  Do not
# rename, reorder, or drop the prefix without updating the matcher.
ERROR_PREFIX = "Error:"
SEARCH_UNAVAILABLE = f"{ERROR_PREFIX} Memory search is temporarily unavailable."
SEARCH_UNEXPECTED = f"{ERROR_PREFIX} Memory search encountered an unexpected error."
RECALL_UNAVAILABLE = f"{ERROR_PREFIX} Memory recall is temporarily unavailable."
RECALL_UNEXPECTED = f"{ERROR_PREFIX} Memory recall encountered an unexpected error."
RECALL_NOT_FOUND_PREFIX = f"{ERROR_PREFIX} Memory not found:"

_INSTRUCTION = (
    "You have access to memory recall tools. Use search_memory "
    "when you need to recall past context, decisions, or learned "
    "information. Use recall_memory to fetch a specific memory by ID."
)

SEARCH_MEMORY_SCHEMA: Final[MappingProxyType[str, JsonValue]] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query.",
            },
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional category filter "
                    "(working, episodic, semantic, procedural, social)."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default 10).",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    }
)

_MAX_MEMORY_ID_LEN: Final[int] = 256

RECALL_MEMORY_SCHEMA: Final[MappingProxyType[str, JsonValue]] = MappingProxyType(
    {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "Exact memory ID to recall.",
                "maxLength": _MAX_MEMORY_ID_LEN,
            },
        },
        "required": ["memory_id"],
    }
)


class ToolBasedInjectionStrategy(ToolBasedReformulationMixin):
    """Tool-based memory injection -- on-demand retrieval via agent tools.

    Implements ``MemoryInjectionStrategy`` protocol.  Instead of
    pre-loading memories, exposes ``search_memory`` and
    ``recall_memory`` tools for the agent to invoke during execution.

    When ``config.query_reformulation_enabled`` is True and both
    ``reformulator`` and ``sufficiency_checker`` are provided, the
    ``search_memory`` handler runs an iterative Search-and-Ask loop:
    retrieve -> check sufficiency -> reformulate query -> re-retrieve,
    up to ``config.max_reformulation_rounds`` rounds.

    Note: Tool-based strategies expose additional methods
    (``handle_tool_call``, ``get_tool_definitions``) beyond the
    base ``MemoryInjectionStrategy`` protocol.  Callers needing
    tool dispatch should type-narrow or check strategy type.

    Args:
        backend: Memory backend for personal memories.
        config: Retrieval pipeline configuration.
        shared_store: Optional shared knowledge store.
        token_estimator: Ignored -- accepted for constructor parity
            with ``ContextInjectionStrategy`` so both strategies can
            be constructed with the same kwargs.  Tool-based retrieval
            has no token estimation step.
        memory_filter: Ignored -- accepted for constructor parity
            with ``ContextInjectionStrategy``.  Callers needing
            tag-based filtering on tool-based retrieval should wrap
            the backend instead.  Use ``ContextInjectionStrategy``
            when post-ranking filtering is required.
        reformulator: ``QueryReformulator`` that produces a new query
            string given the current query and retrieved entries.
            REQUIRED alongside ``sufficiency_checker`` whenever
            ``config.query_reformulation_enabled`` is True -- the
            constructor raises ``ValueError`` if the flag is set but
            either collaborator is missing (fail-fast at wiring time
            rather than silent no-op at retrieval time).  May be
            ``None`` only when reformulation is disabled.
        sufficiency_checker: ``SufficiencyChecker`` that decides
            whether retrieved entries answer the current query.
            Pairs with ``reformulator`` for Search-and-Ask; subject
            to the same constructor guard.

    Raises:
        ValueError: If ``config.query_reformulation_enabled`` is True
            but either ``reformulator`` or ``sufficiency_checker`` is
            missing.
    """

    __slots__ = (
        "_backend",
        "_config",
        "_reformulator",
        "_shared_store",
        "_sufficiency_checker",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        backend: MemoryBackend,
        config: MemoryRetrievalConfig,
        shared_store: SharedKnowledgeStore | None = None,
        token_estimator: TokenEstimator | None = None,  # noqa: ARG002
        memory_filter: MemoryFilterStrategy | None = None,  # noqa: ARG002
        reformulator: QueryReformulator | None = None,
        sufficiency_checker: SufficiencyChecker | None = None,
    ) -> None:
        if config.query_reformulation_enabled and (
            reformulator is None or sufficiency_checker is None
        ):
            msg = (
                "config.query_reformulation_enabled is True but "
                "reformulator and sufficiency_checker must both be "
                "provided to ToolBasedInjectionStrategy; got "
                f"reformulator={reformulator!r}, "
                f"sufficiency_checker={sufficiency_checker!r}"
            )
            raise ValueError(msg)
        self._backend = backend
        self._config = config
        self._shared_store = shared_store
        self._reformulator = reformulator
        self._sufficiency_checker = sufficiency_checker

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,  # noqa: ARG002
        query_text: NotBlankStr,  # noqa: ARG002
        token_budget: int,
    ) -> tuple[ChatMessage, ...]:
        """Return a brief instruction message about available tools.

        Tool-based strategies inject minimal context up front;
        the agent retrieves memories on-demand via tool calls.

        Args:
            agent_id: The agent requesting memories.
            query_text: Text for semantic retrieval (unused).
            token_budget: Maximum tokens for memory content.

        Returns:
            Single instruction message, or empty if budget is zero.
        """
        if token_budget <= 0:
            return ()
        return (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=_INSTRUCTION,
            ),
        )

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return search_memory and recall_memory tool definitions.

        Returns:
            Two tool definitions with JSON Schema parameters.
        """
        # ToolDefinition.parameters_schema expects a plain ``dict``,
        # not ``MappingProxyType``.  dict() unwraps the proxy so each
        # ToolDefinition gets an independent mutable copy -- callers
        # that mutate the schema in-place won't affect the module-level
        # template or other tool definitions built from it.
        return (
            ToolDefinition(
                name=NotBlankStr(SEARCH_MEMORY_TOOL_NAME),
                description=(
                    "Search agent memory for relevant past context, "
                    "decisions, or learned information."
                ),
                parameters_schema=copy.deepcopy(dict(SEARCH_MEMORY_SCHEMA)),
            ),
            ToolDefinition(
                name=NotBlankStr(RECALL_MEMORY_TOOL_NAME),
                description="Recall a specific memory entry by its ID.",
                parameters_schema=copy.deepcopy(dict(RECALL_MEMORY_SCHEMA)),
            ),
        )

    async def handle_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, object],
        agent_id: str,
    ) -> str:
        """Dispatch a tool call to the appropriate handler.

        Args:
            tool_name: Name of the tool being called.
            arguments: Tool arguments from the LLM.
            agent_id: Agent making the call.

        Returns:
            Formatted text result.

        Raises:
            ValueError: If ``tool_name`` is not recognized.
        """
        if tool_name == SEARCH_MEMORY_TOOL_NAME:
            return await self._handle_search(arguments, agent_id)
        if tool_name == RECALL_MEMORY_TOOL_NAME:
            return await self._handle_recall(arguments, agent_id)
        msg = f"Unknown tool: {tool_name!r}"
        logger.warning(
            MEMORY_RETRIEVAL_DEGRADED,
            source="handle_tool_call",
            agent_id=agent_id,
            tool_name=tool_name,
            error=msg,
        )
        raise ValueError(msg)

    async def _handle_search(
        self,
        arguments: dict[str, object],
        agent_id: str,
    ) -> str:
        """Handle a search_memory tool call.

        Returns:
            Result of type ``str``.
        """
        # ``SearchMemoryArgs`` is the tool's declared contract and is
        # enforced at the invoker boundary (see ``SearchMemoryTool``). The
        # strategy stays deliberately LENIENT here rather than re-validating
        # strictly: it clamps ``limit`` and collects invalid ``categories``
        # into ``rejected_categories`` so the search still runs and the
        # invalid values are surfaced back to the LLM for self-correction,
        # instead of hard-failing the whole call on a single bad field.
        query_text, limit, categories, rejected_categories = _parse_search_args(
            arguments,
            self._config.max_memories,
            agent_id=agent_id,
        )
        if query_text is None:
            return f"{ERROR_PREFIX} query must be a non-empty string."
        logger.info(
            MEMORY_RETRIEVAL_START,
            agent_id=agent_id,
            tool=SEARCH_MEMORY_TOOL_NAME,
            query_length=len(query_text),
        )
        entries_or_error = await self._safe_search(
            query_text=query_text,
            limit=limit,
            categories=categories,
            agent_id=agent_id,
        )
        if isinstance(entries_or_error, str):
            return entries_or_error
        logger.info(
            MEMORY_RETRIEVAL_COMPLETE,
            agent_id=agent_id,
            tool=SEARCH_MEMORY_TOOL_NAME,
            ranked_count=len(entries_or_error),
        )
        formatted = _format_entries(entries_or_error)
        if rejected_categories:
            formatted += (
                f"\n\n(Ignored invalid categories: {', '.join(rejected_categories)})"
            )
        return formatted

    async def _safe_search(
        self,
        *,
        query_text: str,
        limit: int,
        categories: frozenset[MemoryCategory] | None,
        agent_id: str,
    ) -> tuple[MemoryEntry, ...] | str:
        """Run the search with error isolation.

        Returns the entries on success, or a user-facing error string
        on ``DomainMemoryError`` / unexpected ``Exception``.  System
        errors (``MemoryError``, ``RecursionError``) propagate.

        Returns:
            Result of type ``tuple[MemoryEntry, ...] | str``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
        try:
            return await self._retrieve_with_reformulation(
                query_text=query_text,
                limit=limit,
                categories=categories,
                agent_id=agent_id,
            )
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source=SEARCH_MEMORY_TOOL_NAME,
                agent_id=agent_id,
                query_length=len(query_text),
                limit=limit,
                error_type="system",
                reason="system_error_in_search",
            )
            raise
        except asyncio.CancelledError:
            raise
        except DomainMemoryError as exc:
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                source=SEARCH_MEMORY_TOOL_NAME,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return SEARCH_UNAVAILABLE
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                MEMORY_RETRIEVAL_DEGRADED,
                exc,
                source=SEARCH_MEMORY_TOOL_NAME,
                agent_id=agent_id,
            )
            return SEARCH_UNEXPECTED

    async def _handle_recall(
        self,
        arguments: dict[str, object],
        agent_id: str,
    ) -> str:
        """Handle a recall_memory tool call.

        Returns:
            Result of type ``str``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
        # Route through the tool's declared ``RecallMemoryArgs`` model
        # (``NotBlankStr`` + ``max_length``) rather than re-extracting the
        # raw dict, so an LLM-hallucinated shape (``{"memory_id": 42}``),
        # a blank id, or an over-length id all fail validation cleanly and
        # surface a friendly message for self-correction. Imported here, not
        # at module level: ``memory.tools.__init__`` back-imports
        # ``ERROR_PREFIX`` from this module, so a top-level import closes a
        # cold-import cycle.
        from synthorg.memory.tools._args import RecallMemoryArgs  # noqa: PLC0415

        try:
            parsed = RecallMemoryArgs.model_validate(arguments)
        except ValidationError:
            return f"{ERROR_PREFIX} memory_id is required."
        memory_id = parsed.memory_id.strip()
        if not memory_id:
            return f"{ERROR_PREFIX} memory_id is required."

        logger.info(
            MEMORY_RETRIEVAL_START,
            agent_id=agent_id,
            tool=RECALL_MEMORY_TOOL_NAME,
            memory_id=memory_id,
        )

        try:
            entry = await self._backend.get(
                NotBlankStr(agent_id),
                NotBlankStr(memory_id),
            )
        except builtins.MemoryError, RecursionError:
            logger.error(
                MEMORY_RETRIEVAL_DEGRADED,
                source=RECALL_MEMORY_TOOL_NAME,
                agent_id=agent_id,
                memory_id=memory_id,
                error_type="system",
                reason="system_error_in_recall",
            )
            raise
        except asyncio.CancelledError:
            raise
        except DomainMemoryError as exc:
            logger.warning(
                MEMORY_RETRIEVAL_DEGRADED,
                source=RECALL_MEMORY_TOOL_NAME,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return RECALL_UNAVAILABLE
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                MEMORY_RETRIEVAL_DEGRADED,
                exc,
                source=RECALL_MEMORY_TOOL_NAME,
                agent_id=agent_id,
            )
            return RECALL_UNEXPECTED

        logger.info(
            MEMORY_RETRIEVAL_COMPLETE,
            agent_id=agent_id,
            tool=RECALL_MEMORY_TOOL_NAME,
            found=entry is not None,
        )

        if entry is None:
            safe_id = memory_id[:64]
            return f"{RECALL_NOT_FOUND_PREFIX} {safe_id}"

        return _format_entries((entry,))

    @property
    def strategy_name(self) -> str:
        """Human-readable strategy identifier.

        Returns:
            ``"tool_based"``.
        """
        return "tool_based"
