"""Agent-facing knowledge-substrate tools.

``SearchKnowledgeTool`` (read) returns cited corpus hits;
``IngestKnowledgeTool`` (admin) registers and indexes a source. Both
bind their project scope per task via :class:`KnowledgeToolFactory`.
"""

from synthorg.tools.knowledge.ingest_knowledge import IngestKnowledgeTool
from synthorg.tools.knowledge.search_knowledge import SearchKnowledgeTool

__all__ = ["IngestKnowledgeTool", "SearchKnowledgeTool"]
