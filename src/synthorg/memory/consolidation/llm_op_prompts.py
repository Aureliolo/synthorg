"""Prompt scaffolding for LLM synthesis consolidation.

Holds the base system prompt (with its untrusted-content directive) and
the concatenation fallback used when the synthesis call fails or returns
empty.  Kept separate from the op so the prompt text and the
deterministic fallback can be exercised in isolation.
"""

from synthorg.engine.prompt_safety import (
    TAG_MEMORY_ENTRY,
    untrusted_content_directive,
)
from synthorg.memory.models import MemoryEntry

BASE_SYSTEM_PROMPT = (
    "You are a memory consolidation assistant. You will receive "
    f"multiple memory entries from the same category, each enclosed "
    f"in <{TAG_MEMORY_ENTRY}>...</{TAG_MEMORY_ENTRY}> tags. Your task "
    "is to:\n"
    "1. Identify duplicate or overlapping information across entries\n"
    "2. Merge semantically related facts into concise statements\n"
    "3. Preserve ALL unique information: specific details, IDs, dates, "
    "names, decisions, and outcomes\n"
    "4. Return a single synthesized summary that is shorter than the "
    "combined input but retains all distinct facts\n\n"
    "Respond with ONLY the synthesized summary, nothing else.\n\n"
    + untrusted_content_directive((TAG_MEMORY_ENTRY,))
)


def fallback_summary(
    entries: tuple[MemoryEntry, ...],
    *,
    truncate_length: int,
) -> str:
    """Concatenate entry contents when LLM synthesis is unavailable.

    Prefixes a category header, then lists each entry's content
    truncated to ``truncate_length`` (with an ellipsis when truncated).

    Args:
        entries: Entries to concatenate (empty yields ``""``).
        truncate_length: Per-entry content character cap.

    Returns:
        Result of type ``str``.
    """
    if not entries:
        return ""
    lines = [f"Consolidated {entries[0].category.value} memories:"]
    for entry in entries:
        truncated = (
            entry.content[:truncate_length] + "..."
            if len(entry.content) > truncate_length
            else entry.content
        )
        lines.append(f"- {truncated}")
    return "\n".join(lines)
