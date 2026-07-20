# module-kind: adapter
"""Org memory presented as the cross-agent shared knowledge store.

:class:`~synthorg.memory.shared.SharedKnowledgeStore` is the seam the
retriever fuses into an agent's recall alongside its personal memories,
and :class:`~synthorg.memory.org.protocol.OrgMemoryBackend` is where
company-wide knowledge already accumulates (offboarding promotion, the
Knowledge-Architect tools, the ontology sync). Adapting one to the other
is what makes the org layer reachable from a working agent's context
rather than only from a tool the agent has to think to call.

Category mapping is deliberate, not incidental: an org fact is a
convention, procedure or definition, which is semantic or procedural
knowledge, never one agent's episode.
"""

from typing import Final

from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.org.models import (
    OrgFact,
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)
from synthorg.memory.org.protocol import OrgMemoryBackend

#: Namespace stamped on adapted facts so a consumer can tell org-sourced
#: entries from an agent's own memories after fusion.
ORG_NAMESPACE: Final[NotBlankStr] = NotBlankStr("org")

#: Confidence assigned to an org fact. Org memory carries no per-fact
#: score, and a fact that survived the org write path is authoritative,
#: so anything below full confidence would understate it against
#: personal memories that self-report.
_ORG_CONFIDENCE: Final[float] = 1.0

_PROCEDURAL_CATEGORIES: Final[frozenset[OrgFactCategory]] = frozenset(
    {OrgFactCategory.PROCEDURE},
)

#: Org facts an agent publishes are conventions by default: a procedure
#: is a deliberate authoring act through the Knowledge-Architect surface,
#: not a by-product of a task.
_DEFAULT_PUBLISH_CATEGORY: Final[OrgFactCategory] = OrgFactCategory.CONVENTION

#: Author role recorded when an agent promotes a memory to org knowledge.
_DEFAULT_AUTHOR_ROLE: Final[NotBlankStr] = NotBlankStr("agent")


def _to_entry(fact: OrgFact) -> MemoryEntry:
    """Present one org fact as a memory entry.

    Returns:
        The fact as a ``MemoryEntry`` owned by its authoring agent, so
        provenance survives fusion and ``exclude_agent`` can act on it.
    """
    author = fact.author.agent_id or NotBlankStr("human")
    category = (
        MemoryCategory.PROCEDURAL
        if fact.category in _PROCEDURAL_CATEGORIES
        else MemoryCategory.SEMANTIC
    )
    return MemoryEntry(
        id=NotBlankStr(str(fact.id)),
        agent_id=author,
        namespace=ORG_NAMESPACE,
        category=category,
        content=fact.content,
        metadata=MemoryMetadata(
            source=NotBlankStr(fact.category.value),
            confidence=_ORG_CONFIDENCE,
            tags=fact.tags,
        ),
        created_at=fact.created_at,
    )


class OrgSharedKnowledgeStore:
    """Cross-agent shared knowledge served by the org memory backend.

    Args:
        backend: The wired org memory backend.
        role: Role recorded as the author's role when an agent publishes.
    """

    def __init__(
        self,
        backend: OrgMemoryBackend,
        *,
        role: NotBlankStr = _DEFAULT_AUTHOR_ROLE,
    ) -> None:
        self._backend = backend
        self._role = role

    async def publish(
        self,
        agent_id: NotBlankStr,
        request: MemoryStoreRequest,
    ) -> NotBlankStr:
        """Promote one memory to company-wide org knowledge.

        Returns:
            The assigned org fact id.

        Raises:
            OrgMemoryAccessDeniedError: If the agent may not write.
            OrgMemoryWriteError: If the write fails.
        """
        return await self._backend.write(
            OrgFactWriteRequest(
                content=request.content,
                category=_DEFAULT_PUBLISH_CATEGORY,
                tags=request.metadata.tags,
            ),
            author=OrgFactAuthor(agent_id=agent_id, role=self._role),
        )

    async def search_shared(
        self,
        query: MemoryQuery,
        *,
        exclude_agent: NotBlankStr | None = None,
    ) -> tuple[MemoryEntry, ...]:
        """Search org knowledge, optionally skipping one author.

        Args:
            query: Search parameters; only text and limit carry over,
                because org memory filters by fact category rather than
                by the memory categories a personal query speaks.
            exclude_agent: Author to omit, normally the querying agent,
                whose own writes already arrive through personal recall.

        Returns:
            Matching org facts as memory entries.

        Raises:
            OrgMemoryQueryError: If the query fails.
        """
        facts = await self._backend.query(
            OrgMemoryQuery(context=query.text, limit=query.limit),
        )
        entries = tuple(_to_entry(fact) for fact in facts)
        if exclude_agent is None:
            return entries
        return tuple(entry for entry in entries if entry.agent_id != exclude_agent)

    async def retract(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Withdraw an org fact.

        Raises:
            FeatureNotImplementedError: Always. Org memory is an
                append-only MVCC log by design, so a fact is superseded
                by writing its replacement, never erased. Failing loud
                here beats reporting a deletion that did not happen.
        """
        msg = (
            f"Org memory is append-only; fact {memory_id!r} cannot be "
            f"retracted by {agent_id!r}. Supersede it with a new fact instead."
        )
        raise FeatureNotImplementedError(msg)


__all__ = ["ORG_NAMESPACE", "OrgSharedKnowledgeStore"]
