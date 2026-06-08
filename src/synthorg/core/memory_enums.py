"""Memory persistence and category enumerations.

Core-local leaf: ``synthorg.core.agent`` (a cold-import leaf) imports
``MemoryLevel`` and ``MemoryCategory``, and ``core`` may not import the
heavy ``synthorg.memory`` hub, so these two live here rather than in
``synthorg.memory.enums``.
"""

from enum import StrEnum


class MemoryLevel(StrEnum):
    """Memory persistence level for an agent (§7.3)."""

    PERSISTENT = "persistent"
    PROJECT = "project"
    SESSION = "session"
    NONE = "none"


class MemoryCategory(StrEnum):
    """Memory type categories for agent memory (§7.2).

    ``PROJECT_DOC`` is a project-scoped (not agent-scoped) category used
    by the living-documentation engine: entries stored under a system docs
    agent_id, scoped via the ``project:<id>`` tag, surfaced via
    ``ProjectAwareMemoryFacade``. ``KNOWLEDGE`` is the corpus-scoped
    knowledge + provenance substrate (ingested external sources, scoped
    via tags, carrying provenance). ``PROJECT_BRAIN`` is the project-scoped
    structured-state store: brain entries under a system brain agent_id,
    scoped via ``project:<id>``, surfaced via the same facade.
    """

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    SOCIAL = "social"
    PROJECT_DOC = "project_doc"
    KNOWLEDGE = "knowledge"
    PROJECT_BRAIN = "project_brain"
