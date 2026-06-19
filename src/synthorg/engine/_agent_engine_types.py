# module-kind: code
"""Boot-time tool-factory provider type aliases for :class:`AgentEngine`.

Extracted from ``agent_engine.py`` so the engine module stays within its
size budget. Each provider is a zero-arg callable the engine resolves at
per-task time, so a memory-gated subsystem (knowledge / docs / research /
project-brain) that wires after the boot engine is constructed is picked
up live rather than captured as a ``None`` at construction.
"""

from collections.abc import Callable

from synthorg.docs_engine.tool_factory import DocsToolFactory
from synthorg.knowledge.tool_factory import KnowledgeToolFactory
from synthorg.project_brain.tool_factory import ProjectBrainToolFactory
from synthorg.research.tool_factory import ResearchToolFactory

type KnowledgeToolFactoryProvider = Callable[[], KnowledgeToolFactory | None]
"""Provider reading the live knowledge tool factory at per-task time."""

type DocsToolFactoryProvider = Callable[[], DocsToolFactory | None]
"""Provider reading the live living-docs tool factory at per-task time."""

type ResearchToolFactoryProvider = Callable[[], ResearchToolFactory | None]
"""Provider reading the live research tool factory at per-task time."""

type BrainToolFactoryProvider = Callable[[], ProjectBrainToolFactory | None]
"""Provider reading the live project-brain tool factory at per-task time."""
