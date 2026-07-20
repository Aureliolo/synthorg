# module-kind: code
"""Deterministic gate between an agent's memory writes and the store.

The agent decides what mattered in its own run, which is what it is good
at. It is unreliable at the other half: the STALE benchmark
(arXiv:2605.06527) found models scoring 76% at recognising an outdated
belief under direct questioning collapsed to 4% when a query merely
presupposed it, with most memory frameworks below 10% overall. Leaving
dedup and staleness to the writer, or to the retriever to notice later,
is the documented production failure mode.

So the gate owns them, and owns them deterministically: no LLM call, no
per-write cost, no non-determinism on a correctness-critical path.

Two deliberate limits follow from that:

* Duplicate detection is lexical. It catches the same fact written twice
  in slightly different words, which is what actually accumulates.
* Supersession is **declared**, never inferred. Deciding from text alone
  that one memory contradicts another is the judgement the literature
  shows is unreliable, so a replacement lands only when the writer names
  the entry it replaces, and a link to an entry that does not exist is
  rejected rather than silently dropped.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.memory.bm25 import tokenize_for_index
from synthorg.memory.models import MemoryEntry

# Dice coefficient over index terms. Chosen so a rewording of the same
# fact collapses while two facts that merely share vocabulary do not;
# callers override per write when they want a different trade-off.
DEFAULT_DEDUP_THRESHOLD: Final[float] = 0.8

# Marks a belief an agent has explicitly replaced. Retrieval excludes
# it, but the entry survives so an audit can still show what was
# believed before and which write replaced it.
SUPERSEDED_TAG: Final[str] = "superseded"
SUPERSEDED_BY_TAG_PREFIX: Final[str] = "superseded_by:"


def is_superseded(entry: MemoryEntry) -> bool:
    """Whether *entry* has been replaced by a later write.

    A superseded entry is retained for audit but must never be recalled:
    coexisting stale and fresh beliefs "without arbitration" is the
    production failure the write-time gate exists to prevent.

    Args:
        entry: The candidate memory.

    Returns:
        ``True`` when the entry has been retired.
    """
    return SUPERSEDED_TAG in entry.metadata.tags


class WriteDisposition(StrEnum):
    """What the gate decided to do with a candidate memory.

    Attributes:
        ADD: Store as a new entry.
        NOOP: Drop; an equivalent entry already exists.
        SUPERSEDE: Store, and mark the named prior entry replaced.
        REJECT: Refuse; the candidate is unusable as written.
    """

    ADD = "add"
    NOOP = "noop"
    SUPERSEDE = "supersede"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class WriteDecision:
    """The gate's verdict on one candidate write.

    Attributes:
        disposition: What to do with the candidate.
        duplicate_of: Closest existing entry when ``NOOP``.
        supersedes: Entry being replaced when ``SUPERSEDE``.
        reason: Why the write was refused when ``REJECT``.
    """

    disposition: WriteDisposition
    duplicate_of: str | None = None
    supersedes: str | None = None
    reason: str | None = None


def content_similarity(left: str, right: str) -> float:
    """Score two memory texts by term overlap.

    Dice over the same tokenisation the durable backend indexes with, so
    the gate and the index agree on what a term is.

    Args:
        left: First text.
        right: Second text.

    Returns:
        Similarity in [0, 1]; 1.0 when both tokenise to the same terms.
    """
    left_terms = set(tokenize_for_index(left))
    right_terms = set(tokenize_for_index(right))
    if not left_terms and not right_terms:
        return 1.0
    if not left_terms or not right_terms:
        return 0.0
    shared = len(left_terms & right_terms)
    return 2 * shared / (len(left_terms) + len(right_terms))


def evaluate_write(
    candidate: str,
    *,
    existing: tuple[MemoryEntry, ...],
    supersedes: NotBlankStr | None = None,
    dedup_threshold: float = DEFAULT_DEDUP_THRESHOLD,
) -> WriteDecision:
    """Decide what to do with a candidate memory write.

    Args:
        candidate: The text the agent wants to remember.
        existing: Entries already stored for this agent that the caller
            considers comparable (typically the top semantic matches).
        supersedes: Identifier of an entry this write replaces, when the
            writer explicitly claims one.
        dedup_threshold: Similarity at or above which the candidate is
            treated as already stored.

    Returns:
        The gate's decision.
    """
    if not candidate.strip():
        return WriteDecision(
            disposition=WriteDisposition.REJECT,
            reason="candidate content is blank",
        )

    if supersedes is not None:
        known = {str(entry.id) for entry in existing}
        if str(supersedes) not in known:
            return WriteDecision(
                disposition=WriteDisposition.REJECT,
                reason=(
                    "supersedes names an entry that is not among the "
                    "comparable existing entries"
                ),
            )
        # An explicit replacement outranks dedup: the writer is asserting
        # the prior entry is wrong, and re-recording that is the point.
        return WriteDecision(
            disposition=WriteDisposition.SUPERSEDE,
            supersedes=str(supersedes),
        )

    closest = _closest_match(candidate, existing)
    if closest is not None and closest[1] >= dedup_threshold:
        return WriteDecision(
            disposition=WriteDisposition.NOOP,
            duplicate_of=closest[0],
        )
    return WriteDecision(disposition=WriteDisposition.ADD)


def _closest_match(
    candidate: str,
    existing: tuple[MemoryEntry, ...],
) -> tuple[str, float] | None:
    """Find the most similar existing entry.

    Returns:
        An ``(entry_id, similarity)`` pair, or ``None`` when there are no
        entries to compare against.
    """
    if not existing:
        return None
    scored = [
        (str(entry.id), content_similarity(candidate, entry.content))
        for entry in existing
    ]
    # Ties break on id so the decision is reproducible across runs.
    return max(scored, key=lambda pair: (pair[1], pair[0]))
