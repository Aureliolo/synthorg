# module-kind: code
"""Gated archival writes for the self-editing memory strategy.

Split from ``self_editing`` so the strategy module stays within its size
budget and the write path, which is where correctness is decided, is
reviewable on its own.

The agent proposes; :mod:`synthorg.memory.write_gate` disposes. These
helpers are the two halves the gate needs from the store: the entries a
candidate should be judged against, and the retirement of a belief the
writer has explicitly replaced.
"""

import builtins

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import (
    MemoryEntry,
    MemoryQuery,
    MemoryUpdateRequest,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.tool_retriever import ERROR_PREFIX
from synthorg.memory.write_gate import (
    SUPERSEDED_BY_TAG_PREFIX,
    SUPERSEDED_TAG,
    WriteDecision,
    WriteDisposition,
    evaluate_write,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_WRITE_GATE_DECIDED,
    MEMORY_WRITE_GATE_DEGRADED,
)

logger = get_logger(__name__)


async def comparable_entries(
    backend: MemoryBackend,
    agent_id: NotBlankStr,
    content: NotBlankStr,
    category: MemoryCategory,
    limit: int,
) -> tuple[MemoryEntry, ...]:
    """Fetch the entries a candidate write should be judged against.

    Scoped to the same category: a semantic fact and a procedural lesson
    that happen to share wording are not duplicates of one another.

    Args:
        backend: The store to read from.
        agent_id: Owning agent.
        content: The candidate text.
        category: Category the candidate would be stored under.
        limit: How many comparable entries to fetch.

    Returns:
        Comparable entries, empty when retrieval fails. Failing open
        risks a duplicate; failing closed would drop a real memory,
        which is the worse loss.

    Raises:
        MemoryError: If the related operation fails.
        RecursionError: If the related operation fails.
    """
    try:
        return await backend.retrieve(
            agent_id,
            MemoryQuery(
                text=content,
                categories=frozenset({category}),
                limit=limit,
            ),
        )
    except builtins.MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MEMORY_WRITE_GATE_DEGRADED,
            agent_id=agent_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()


async def gate_archival_write(  # noqa: PLR0913 -- the gate's full input surface
    backend: MemoryBackend,
    agent_id: NotBlankStr,
    content: NotBlankStr,
    category: MemoryCategory,
    *,
    supersedes: NotBlankStr | None,
    candidates: int,
) -> tuple[WriteDecision, str | None]:
    """Run a candidate archival write past the deterministic gate.

    Args:
        backend: The store to compare against.
        agent_id: Owning agent.
        content: The candidate text.
        category: Category the candidate would be stored under.
        supersedes: Entry the writer claims this replaces, if any.
        candidates: How many existing entries to deduplicate against.

    Returns:
        The decision, plus a caller response when the write must not
        proceed (``None`` when the caller should go on to store).
    """
    decision = evaluate_write(
        content,
        existing=await comparable_entries(
            backend, agent_id, content, category, candidates
        ),
        supersedes=supersedes,
        supersedes_exists=(
            supersedes is not None
            and await backend.get(agent_id, supersedes) is not None
        ),
    )
    logger.info(
        MEMORY_WRITE_GATE_DECIDED,
        agent_id=agent_id,
        category=category.value,
        disposition=decision.disposition.value,
        duplicate_of=decision.duplicate_of,
        supersedes=decision.supersedes,
    )
    match decision.disposition:
        case WriteDisposition.REJECT:
            return decision, f"{ERROR_PREFIX} {decision.reason}."
        case WriteDisposition.NOOP:
            return decision, (
                "Already remembered; nothing stored "
                f"(existing id={decision.duplicate_of})."
            )
        case WriteDisposition.ADD | WriteDisposition.SUPERSEDE:
            return decision, None


async def retire_superseded(
    backend: MemoryBackend,
    agent_id: NotBlankStr,
    superseded_id: NotBlankStr,
    replacement_id: str,
) -> bool:
    """Tag a replaced entry so recall stops surfacing it.

    Tagging rather than deleting: the belief was held, and an audit that
    cannot show what was believed before is worth less than the space it
    saves.

    Args:
        backend: The store to update.
        agent_id: Owning agent.
        superseded_id: The entry being replaced.
        replacement_id: The entry replacing it.

    Returns:
        ``True`` when the prior entry was retired.
    """
    entry = await backend.get(agent_id, superseded_id)
    if entry is None:
        return False
    updated_tags = (
        *entry.metadata.tags,
        NotBlankStr(SUPERSEDED_TAG),
        NotBlankStr(f"{SUPERSEDED_BY_TAG_PREFIX}{replacement_id}"),
    )
    result = await backend.update(
        agent_id,
        superseded_id,
        MemoryUpdateRequest(
            metadata=entry.metadata.model_copy(update={"tags": updated_tags})
        ),
    )
    return result is not None
