# module-kind: declarative
"""The seams that extend an agent's tool surface past the base registry.

Every field here is a LATE-BOUND provider rather than a factory, and that
is load-bearing: the slices behind them (project brain, knowledge, docs,
research, structure map) wire AFTER the boot engine is built, so a
captured factory would be ``None`` for the life of the process.
``AgentEngine._make_tool_invoker`` asks each one per task instead.
"""

from dataclasses import dataclass

from synthorg.engine._agent_engine_types import (
    BrainToolFactoryProvider,
    DocsToolFactoryProvider,
    KnowledgeToolFactoryProvider,
    ResearchToolFactoryProvider,
    StructureMapToolFactoryProvider,
)
from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes
from synthorg.tools.external_api._runtime import ExternalApiRuntime
from synthorg.tools.invocation_tracker import ToolInvocationTracker


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineTooling:
    """What is added to the registry an agent starts from.

    Attributes:
        external_api_runtime: Backs the governed external-access tool, or
            ``None`` when the feature is off or no catalog is wired.
        connection_tool_runtimes: The per-family connection tool runtimes.
            An empty ``ConnectionToolRuntimes()`` is the declared "no
            connections", never an omission.
        tool_invocation_tracker: Records every invocation, or ``None``.
        brain_tool_factory_provider: Project-brain tools, added only when
            the task carries a project scope.
        knowledge_tool_factory_provider: Knowledge-substrate tools.
        docs_tool_factory_provider: Living-document tools, added only
            with a project scope (they bind both the project and the
            author).
        research_tool_factory_provider: The research tool.
        structure_map_tool_factory_provider: The structure-map query
            tool, parked by brownfield intake and absent until a codebase
            has been imported.
    """

    external_api_runtime: ExternalApiRuntime | None
    connection_tool_runtimes: ConnectionToolRuntimes
    tool_invocation_tracker: ToolInvocationTracker | None
    brain_tool_factory_provider: BrainToolFactoryProvider | None
    knowledge_tool_factory_provider: KnowledgeToolFactoryProvider | None
    docs_tool_factory_provider: DocsToolFactoryProvider | None
    research_tool_factory_provider: ResearchToolFactoryProvider | None
    structure_map_tool_factory_provider: StructureMapToolFactoryProvider | None


__all__ = ["EngineTooling"]
