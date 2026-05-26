"""Memory tool wrappers for ToolRegistry integration.

Provides ``SearchMemoryTool`` and ``RecallMemoryTool`` for
``ToolBasedInjectionStrategy``, six self-editing tools
(``CoreMemoryReadTool``, ``CoreMemoryWriteTool``,
``ArchivalMemorySearchTool``, ``ArchivalMemoryWriteTool``,
``RecallMemoryReadTool``, ``RecallMemoryWriteTool``) for
``SelfEditingMemoryStrategy``, and six ``KnowledgeArchitect*Tool``
classes for the Knowledge Architect role.

All tool classes are thin ``BaseTool`` subclasses that delegate
execution to ``strategy.handle_tool_call()``, bridging the memory
injection system into the standard tool dispatch pipeline
(``ToolInvoker`` -> ``ToolRegistry`` -> ``BaseTool.execute``).
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.self_editing import SelfEditingMemoryStrategy
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from synthorg.memory.tools._shared import _is_error_response
from synthorg.memory.tools.archival import (
    ArchivalMemorySearchTool,
    ArchivalMemoryWriteTool,
)
from synthorg.memory.tools.core import CoreMemoryReadTool, CoreMemoryWriteTool
from synthorg.memory.tools.knowledge_architect import (
    KnowledgeArchitectBrowseWikiTool,
    KnowledgeArchitectDeleteTool,
    KnowledgeArchitectGuideTool,
    KnowledgeArchitectReadTool,
    KnowledgeArchitectSearchTool,
    KnowledgeArchitectWriteTool,
)
from synthorg.memory.tools.recall import RecallMemoryReadTool, RecallMemoryWriteTool
from synthorg.memory.tools.recall_search import RecallMemoryTool
from synthorg.memory.tools.search import SearchMemoryTool
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import (
    TOOL_FACTORY_BUILT,
    TOOL_MEMORY_AUGMENTATION_FAILED,
    TOOL_REGISTRY_BUILT,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.memory.injection import MemoryInjectionStrategy
    from synthorg.tools.base import BaseTool
    from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)


__all__ = (
    "ArchivalMemorySearchTool",
    "ArchivalMemoryWriteTool",
    "CoreMemoryReadTool",
    "CoreMemoryWriteTool",
    "KnowledgeArchitectBrowseWikiTool",
    "KnowledgeArchitectDeleteTool",
    "KnowledgeArchitectGuideTool",
    "KnowledgeArchitectReadTool",
    "KnowledgeArchitectSearchTool",
    "KnowledgeArchitectWriteTool",
    "RecallMemoryReadTool",
    "RecallMemoryTool",
    "RecallMemoryWriteTool",
    "SearchMemoryTool",
    "_is_error_response",
    "create_memory_tools",
    "create_self_editing_tools",
    "registry_with_memory_tools",
)


def create_self_editing_tools(
    *,
    strategy: SelfEditingMemoryStrategy,
    agent_id: NotBlankStr,
) -> tuple[BaseTool, ...]:
    """Create self-editing memory tools for a specific agent.

    Returns all six self-editing tools bound to the given ``agent_id``
    and sharing the provided strategy instance.

    Args:
        strategy: Self-editing memory strategy with backend access.
        agent_id: Agent ID to bind to the tools.

    Returns:
        Tuple of six ``BaseTool`` instances.
    """
    tools = (
        CoreMemoryReadTool(strategy=strategy, agent_id=agent_id),
        CoreMemoryWriteTool(strategy=strategy, agent_id=agent_id),
        ArchivalMemorySearchTool(strategy=strategy, agent_id=agent_id),
        ArchivalMemoryWriteTool(strategy=strategy, agent_id=agent_id),
        RecallMemoryReadTool(strategy=strategy, agent_id=agent_id),
        RecallMemoryWriteTool(strategy=strategy, agent_id=agent_id),
    )
    logger.debug(
        TOOL_FACTORY_BUILT,
        agent_id=agent_id,
        tools=[t.name for t in tools],
    )
    return tools


def create_memory_tools(
    *,
    strategy: ToolBasedInjectionStrategy,
    agent_id: NotBlankStr,
) -> tuple[BaseTool, ...]:
    """Create memory tools for a specific agent.

    Returns ``SearchMemoryTool`` and ``RecallMemoryTool`` bound to the
    given ``agent_id`` and sharing the provided strategy instance.

    Args:
        strategy: Tool-based injection strategy with backend access.
        agent_id: Agent ID to bind to the tools.

    Returns:
        Tuple of two ``BaseTool`` instances (search and recall).
    """
    tools = (
        SearchMemoryTool(strategy=strategy, agent_id=agent_id),
        RecallMemoryTool(strategy=strategy, agent_id=agent_id),
    )
    logger.debug(
        TOOL_FACTORY_BUILT,
        agent_id=agent_id,
        tools=[t.name for t in tools],
    )
    return tools


def _build_augmented_registry(
    tool_registry: ToolRegistry,
    strategy: ToolBasedInjectionStrategy,
    agent_id: NotBlankStr,
) -> ToolRegistry:
    """Construct a new registry with memory tools appended.

    Returns:
        Result of type ``ToolRegistry``.
    """
    from synthorg.tools.registry import (  # noqa: PLC0415
        ToolRegistry as _ToolRegistry,
    )

    memory_tools = create_memory_tools(
        strategy=strategy,
        agent_id=agent_id,
    )
    existing = list(tool_registry.all_tools())
    return _ToolRegistry([*existing, *memory_tools])


def _build_self_editing_registry(
    tool_registry: ToolRegistry,
    strategy: SelfEditingMemoryStrategy,
    agent_id: NotBlankStr,
) -> ToolRegistry:
    """Construct a new registry with self-editing tools appended.

    Returns:
        Result of type ``ToolRegistry``.
    """
    from synthorg.tools.registry import (  # noqa: PLC0415
        ToolRegistry as _ToolRegistry,
    )

    self_editing_tools = create_self_editing_tools(
        strategy=strategy,
        agent_id=agent_id,
    )
    existing = list(tool_registry.all_tools())
    return _ToolRegistry([*existing, *self_editing_tools])


def registry_with_memory_tools(
    tool_registry: ToolRegistry,
    strategy: MemoryInjectionStrategy | None,
    agent_id: NotBlankStr,
) -> ToolRegistry:
    """Build a registry with memory tools added if applicable.

    Returns the original registry unchanged when the strategy is
    ``None`` or is not a memory tool strategy.  Handles both
    ``ToolBasedInjectionStrategy`` (adds 2 tools) and
    ``SelfEditingMemoryStrategy`` (adds 6 tools).  Follows the
    ``registry_with_approval_tool`` pattern in
    ``engine/_security_factory.py``.

    Args:
        tool_registry: Base tool registry.
        strategy: Memory injection strategy (may be any type or None).
        agent_id: Agent ID to bind to the memory tools.

    Returns:
        Augmented registry with memory tools, or original if not
        applicable.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if isinstance(strategy, SelfEditingMemoryStrategy):
        try:
            augmented = _build_self_editing_registry(tool_registry, strategy, agent_id)
        except ValueError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TOOL_MEMORY_AUGMENTATION_FAILED,
                source="registry_augmentation",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return tool_registry
        logger.debug(
            TOOL_REGISTRY_BUILT,
            tool_count=len(augmented),
            tools=augmented.list_tools(),
        )
        return augmented

    if not isinstance(strategy, ToolBasedInjectionStrategy):
        return tool_registry

    try:
        augmented = _build_augmented_registry(
            tool_registry,
            strategy,
            agent_id,
        )
    except ValueError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            TOOL_MEMORY_AUGMENTATION_FAILED,
            source="registry_augmentation",
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return tool_registry

    logger.debug(
        TOOL_REGISTRY_BUILT,
        tool_count=len(augmented),
        tools=augmented.list_tools(),
    )
    return augmented
