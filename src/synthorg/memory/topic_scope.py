# module-kind: code
"""Topic scoping for procedural recall.

Procedural lessons are task-specific: a lesson about recovering a
checkout flow helps a checkout task and pollutes anything else. Deciding
which is which by similarity score does not work. Measured against the
benchmark brief suite, a checkout lesson shares *more* terms with an
unrelated "retrieval reranking" brief (4) than with the checkout brief
it came from (2), and every normalisation tried (query coverage, entry
coverage, Dice, Jaccard) ranks the unrelated brief at or above the true
one. There is no threshold to pick, which is the same conclusion the
applied-threshold literature reaches for embedding similarity.

So scoping keys on structure the lesson already carries: its tags. A
lesson tagged ``checkout`` reaches a task whose title mentions checkout
and abstains elsewhere. Abstention is the intended outcome, not a
failure to retrieve.

It applies only where it is needed. Tag overlap is itself a lexical
test, so on a backend with real semantic recall it would throw away the
better signal: a lesson about rolling back a deployment shares no term
with "revert the release", and gating on tags would drop precisely the
match dense retrieval was bought to find. Where meaning-similarity is
available it already establishes topicality; the tag gate exists to
compensate for its absence.
"""

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.bm25 import tokenize_for_index
from synthorg.memory.models import MemoryEntry
from synthorg.memory.ranking import ScoredMemory
from synthorg.memory.recall_request import MemoryRecallRequest
from synthorg.memory.write_gate import is_superseded


def scope_terms(request: MemoryRecallRequest) -> frozenset[str]:
    """Derive the topic terms a procedural lesson must match.

    Only the task title is used. The rest of the recall context (role,
    department, objective) widens *recall*, which is what it is for, but
    it is org-wide vocabulary and would match every lesson, scoping
    nothing.

    Args:
        request: The recall context.

    Returns:
        Lowercase topic terms, empty when the title carries none.
    """
    return frozenset(tokenize_for_index(request.task_title))


def admissible(
    ranked: tuple[ScoredMemory, ...],
    *,
    terms: frozenset[str],
    scope_applies: bool,
) -> tuple[ScoredMemory, ...]:
    """Drop candidates that must not reach the prompt.

    Both tests are cheap and structural, so they run before any
    reranking: an entry excluded here should not cost a downstream
    stage anything. A superseded entry is a belief the agent has already
    replaced, and recalling it would put a known-stale claim back in
    front of the model.

    Args:
        ranked: Candidates in ranked order.
        terms: Topic terms from :func:`scope_terms`.
        scope_applies: Whether topic scoping is in force for this
            backend.

    Returns:
        The admissible candidates, order preserved.
    """
    return tuple(
        memory
        for memory in ranked
        if (not scope_applies or in_topic_scope(memory.entry, terms))
        and not is_superseded(memory.entry)
    )


def in_topic_scope(entry: MemoryEntry, terms: frozenset[str]) -> bool:
    """Whether *entry* is on-topic for the given scope terms.

    Non-procedural categories are not task-specific, so they pass
    unchanged. An untagged procedural entry also passes: there is no
    structure to scope on, and dropping it would silently disable every
    lesson whose proposer emitted no tags, which is the same
    silent-degradation failure this design exists to avoid.

    Args:
        entry: The candidate memory.
        terms: Topic terms from :func:`scope_terms`.

    Returns:
        ``True`` when the entry may be injected for this task.
    """
    if entry.category is not MemoryCategory.PROCEDURAL:
        return True
    if not entry.metadata.tags or not terms:
        return True
    return bool({tag.lower() for tag in entry.metadata.tags} & terms)
