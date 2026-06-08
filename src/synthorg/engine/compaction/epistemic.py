"""Epistemic marker detection for compaction summaries.

Detects reasoning markers (hedging, reconsideration, uncertainty,
verification, correction) in assistant messages.  Messages with
high marker density are preserved during compaction to maintain
reasoning chain integrity.

Reference: arXiv:2603.24472 -- removing epistemic markers degrades
accuracy by up to 63% on complex reasoning tasks.
"""

import re
from typing import Final

from synthorg.core.task_enums import Complexity
from synthorg.observability import get_logger

logger = get_logger(__name__)

# Precompiled case-insensitive word-boundary patterns grouped by type.
_HEDGING = re.compile(r"\b(wait|hmm|hm|ah)\b", re.IGNORECASE)
_RECONSIDERATION = re.compile(
    r"\b(actually|let me reconsider|on second thought|I was wrong)\b",
    re.IGNORECASE,
)
_UNCERTAINTY = re.compile(
    r"\b(perhaps|alternatively|I'm not sure|uncertain)\b",
    re.IGNORECASE,
)
_VERIFICATION = re.compile(
    r"\b(check|verify|double-check|let me verify)\b",
    re.IGNORECASE,
)
_CORRECTION = re.compile(
    r"\b(but wait|actually no|hold on)\b",
    re.IGNORECASE,
)

EPISTEMIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    _HEDGING,
    _RECONSIDERATION,
    _UNCERTAINTY,
    _VERIFICATION,
    _CORRECTION,
)

# Sentence-splitting regex: split on period, question mark, exclamation,
# or newline followed by optional whitespace.
_SENTENCE_SPLIT = re.compile(r"[.?!]\s+|\n+")

# Separator emitted between preserved sentences in the extraction output.
_SENTENCE_SEPARATOR = "; "

# Complexity levels that use the low-threshold (>= 1 marker).
_HIGH_COMPLEXITY = frozenset({Complexity.COMPLEX, Complexity.EPIC})

# Marker count thresholds per complexity tier.
_COMPLEX_THRESHOLD = 1
_SIMPLE_THRESHOLD: Final[int] = 3


def count_epistemic_markers(text: str) -> int:
    """Count distinct epistemic pattern matches in text.

    Each pattern is counted once regardless of how many times it
    matches within the text.

    Args:
        text: Input text to scan.

    Returns:
        Number of distinct patterns that matched (0 to 5).
    """
    return sum(1 for p in EPISTEMIC_PATTERNS if p.search(text))


def should_preserve_message(
    text: str,
    complexity: Complexity,
) -> bool:
    """Decide whether a message should be preserved during compaction.

    Uses complexity-adaptive thresholds:
    - COMPLEX/EPIC: preserve if >= 1 epistemic marker
    - SIMPLE/MEDIUM: preserve if >= 3 epistemic markers

    Args:
        text: Message content.
        complexity: Task complexity level.

    Returns:
        True if the message should be preserved verbatim.
    """
    count = count_epistemic_markers(text)
    threshold = (
        _COMPLEX_THRESHOLD if complexity in _HIGH_COMPLEXITY else _SIMPLE_THRESHOLD
    )
    return count >= threshold


_DEFAULT_MARKER_MAX_CHARS: Final[int] = 200


def extract_marker_sentences(
    text: str,
    *,
    max_chars: int = _DEFAULT_MARKER_MAX_CHARS,
) -> str:
    """Extract sentences containing epistemic markers.

    Splits text on sentence boundaries (period, question mark,
    exclamation, newline) and collects sentences where at least
    one epistemic pattern matches.  Truncates the result to
    *max_chars*.

    Args:
        text: Input text.
        max_chars: Maximum character length of the result.

    Returns:
        Joined marker-containing sentences, truncated if needed.
    """
    sentences = _SENTENCE_SPLIT.split(text)
    marker_sentences: list[str] = []
    total_len = 0

    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            continue
        if any(p.search(stripped) for p in EPISTEMIC_PATTERNS):
            sep_len = len(_SENTENCE_SEPARATOR) if marker_sentences else 0
            if total_len + sep_len + len(stripped) > max_chars:
                # If this is the first sentence and it exceeds max_chars,
                # include a truncated version rather than returning empty.
                if not marker_sentences:
                    marker_sentences.append(stripped[:max_chars])
                break
            marker_sentences.append(stripped)
            total_len += sep_len + len(stripped)

    if not marker_sentences:
        return ""

    return _SENTENCE_SEPARATOR.join(marker_sentences)
