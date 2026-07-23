# module-kind: code
"""Factory for a read-only memory-recall tool granted to agent sessions.

Two owner-run sessions need to recall memory with a tool rather than through
pre-injected context: the owner-run planning session (so a plan builds on past
retros and org playbooks) and the SHIP-time retrospective session (so the lead
avoids restating what the organisation already recorded). Both want the same
thing: a ``search_memory`` tool bound to the acting agent, fusing the agent's
own memory with company-wide org knowledge.

The tool is read-only (``memory:read``), so it survives a session's read-only
tool filter, and it delegates to the shared tool-based retrieval strategy, so
recall behaves identically to the execution-time memory hot path.
"""

from synthorg.core.types import NotBlankStr
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.shared_store import OrgSharedKnowledgeStore
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from synthorg.memory.tools.search import SearchMemoryTool


def build_memory_recall_tool(
    *,
    backend: MemoryBackend,
    agent_id: NotBlankStr,
    org_backend: OrgMemoryBackend | None = None,
    config: MemoryRetrievalConfig | None = None,
) -> SearchMemoryTool:
    """Build a ``search_memory`` tool bound to *agent_id*.

    Args:
        backend: The agent-memory backend the recall reads from.
        agent_id: The acting agent the tool is bound to (recall is per-agent).
        org_backend: When present, org knowledge is fused into recall through
            :class:`OrgSharedKnowledgeStore`; when absent, only personal memory
            is searched.
        config: Retrieval tuning; a shared default when omitted.

    Returns:
        A read-only ``search_memory`` tool ready to grant to a session.
    """
    shared_store = (
        OrgSharedKnowledgeStore(org_backend) if org_backend is not None else None
    )
    strategy = ToolBasedInjectionStrategy(
        backend=backend,
        config=config or MemoryRetrievalConfig(include_shared=org_backend is not None),
        shared_store=shared_store,
    )
    return SearchMemoryTool(strategy=strategy, agent_id=agent_id)
