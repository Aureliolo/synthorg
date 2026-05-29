"""Tool-based ontology injection strategy.

Exposes a ``lookup_entity`` tool that agents call on demand to
retrieve entity definitions.  No prompt injection -- agents
discover entities through the tool interface.
"""

from typing import TYPE_CHECKING, Any, ClassVar, override

from pydantic import BaseModel

from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.ontology import (
    ONTOLOGY_INJECTION_PREPARED,
    ONTOLOGY_TOOL_LOOKUP,
)
from synthorg.ontology.errors import OntologyNotFoundError
from synthorg.ontology.injection._tool_args import LookupEntityArgs
from synthorg.ontology.injection.prompt import format_entity
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.ontology_protocol import OntologyEntityRepository
    from synthorg.providers.models import ChatMessage, ToolDefinition

logger = get_logger(__name__)

LOOKUP_ENTITY_TOOL_NAME = "lookup_entity"
"""Default tool name for entity lookup."""


class LookupEntityTool(BaseTool):
    """On-demand entity definition lookup tool.

    Delegates to ``OntologyEntityRepository.get()`` for exact name lookup
    and ``OntologyEntityRepository.search()`` for free-text search.

    Args:
        backend: Ontology backend for entity retrieval.
        tool_name: Override the default tool name.
    """

    args_model: ClassVar[type[BaseModel] | None] = LookupEntityArgs

    def __init__(
        self,
        *,
        backend: OntologyEntityRepository,
        tool_name: str = LOOKUP_ENTITY_TOOL_NAME,
    ) -> None:
        super().__init__(
            name=tool_name,
            description=(
                "Look up entity definitions from the organizational "
                "ontology. Use 'name' for exact lookup or 'query' for "
                "free-text search across entity names and definitions."
            ),
            parameters_schema=LookupEntityArgs.model_json_schema(),
            category=ToolCategory.ONTOLOGY,
        )
        self._backend = backend

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute entity lookup or search.

        Args:
            arguments: Tool arguments with ``name`` or ``query``.

        Returns:
            Formatted entity definition(s) or error message.
        """
        name = arguments.get("name")
        query = arguments.get("query")

        if name and query:
            return ToolExecutionResult(
                content="Provide exactly one of 'name' or 'query', not both.",
                is_error=True,
            )
        if name:
            return await self._lookup_by_name(name)
        if query:
            return await self._search(query)
        return ToolExecutionResult(
            content="Provide either 'name' for exact lookup or 'query' for search.",
            is_error=True,
        )

    async def _lookup_by_name(self, name: str) -> ToolExecutionResult:
        """Look up a single entity by exact name.

        Args:
            name: Entity name to look up.

        Returns:
            Formatted entity or not-found error.
        """
        try:
            entity = await self._backend.get(name)
        except MemoryError, RecursionError:
            raise
        except OntologyNotFoundError:
            entity = None
        except Exception as exc:
            logger.warning(
                ONTOLOGY_TOOL_LOOKUP,
                name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content="Entity lookup failed. Try again later.",
                is_error=True,
            )
        if entity is None:
            logger.debug(
                ONTOLOGY_TOOL_LOOKUP,
                name=name,
                found=False,
            )
            return ToolExecutionResult(
                content=f"Entity '{name}' not found in the ontology.",
                is_error=True,
            )
        logger.debug(
            ONTOLOGY_TOOL_LOOKUP,
            name=name,
            found=True,
        )
        return ToolExecutionResult(
            content=format_entity(entity),
        )

    async def _search(self, query: str) -> ToolExecutionResult:
        """Search entities by free-text query.

        Args:
            query: Search query string.

        Returns:
            Formatted list of matching entities or empty result.
        """
        try:
            results = await self._backend.search(query)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                ONTOLOGY_TOOL_LOOKUP,
                query=query,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content="Entity search failed. Try again later.",
                is_error=True,
            )
        if not results:
            logger.debug(
                ONTOLOGY_TOOL_LOOKUP,
                query=query,
                result_count=0,
            )
            return ToolExecutionResult(
                content=f"No entities match query '{query}'.",
            )
        logger.debug(
            ONTOLOGY_TOOL_LOOKUP,
            query=query,
            result_count=len(results),
        )
        formatted = "\n\n".join(format_entity(e) for e in results)
        return ToolExecutionResult(
            content=formatted,
            metadata={"result_count": len(results)},
        )


class ToolBasedInjectionStrategy:
    """On-demand entity retrieval via the ``lookup_entity`` tool.

    No prompt injection -- agents discover entity definitions by
    calling the tool.  The tool is registered in the agent's
    ``ToolRegistry`` during execution setup.

    Args:
        backend: Ontology backend for entity retrieval.
        tool_name: Override the default tool name.
    """

    def __init__(
        self,
        *,
        backend: OntologyEntityRepository,
        tool_name: str = LOOKUP_ENTITY_TOOL_NAME,
    ) -> None:
        self._backend = backend
        self._tool = LookupEntityTool(
            backend=backend,
            tool_name=tool_name,
        )

    async def prepare_messages(
        self,
        agent_id: NotBlankStr,
        task_context: NotBlankStr,  # noqa: ARG002
        token_budget: int,  # noqa: ARG002
    ) -> tuple[ChatMessage, ...]:
        """Tool strategy injects no messages.

        Args:
            agent_id: The agent requesting ontology context.
            task_context: Current task description.
            token_budget: Maximum tokens (unused).

        Returns:
            Empty tuple.
        """
        logger.debug(
            ONTOLOGY_INJECTION_PREPARED,
            agent_id=agent_id,
            entity_count=0,
            strategy="tool",
        )
        return ()

    def get_tool_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return the ``lookup_entity`` tool definition.

        Returns:
            Single-element tuple with the tool definition.
        """
        return (self._tool.to_definition(),)

    @property
    def strategy_name(self) -> str:
        """Return ``"tool"``."""
        return "tool"

    @property
    def tool(self) -> LookupEntityTool:
        """Return the underlying ``LookupEntityTool`` instance.

        Callers that need the ``BaseTool`` subclass (e.g. for
        ``ToolRegistry`` registration) use this property.
        """
        return self._tool
