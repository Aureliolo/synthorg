"""Content density classification for dual-mode archival.

Heuristic-based classifier that determines whether memory content is
sparse (conversational, narrative) or dense (code, structured data,
identifiers).  Classification is deterministic -- no LLM calls.
"""

import re
from enum import StrEnum
from typing import Final

from synthorg.memory.models import MemoryEntry  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import (
    DENSITY_CLASSIFICATION_COMPLETE,
)

logger = get_logger(__name__)


class ContentDensity(StrEnum):
    """Classification of memory content density.

    Determines the archival mode: sparse content receives abstractive
    LLM summarization, dense content receives extractive preservation.
    """

    SPARSE = "sparse"
    """Conversational, narrative, low information density."""

    DENSE = "dense"
    """Code, structured data, identifiers, high information density."""


# ── Heuristic signal weights ────────────────────────────────────

_WEIGHT_CODE: Final[float] = 0.30
_WEIGHT_STRUCTURED: Final[float] = 0.25
_WEIGHT_IDENTIFIERS: Final[float] = 0.20
_WEIGHT_NUMERIC: Final[float] = 0.10
_WEIGHT_LINE_STRUCTURE: Final[float] = 0.15

_WEIGHT_SUM_TOLERANCE: Final[float] = 1e-9

# Guard: weights must sum to 1.0 for threshold interpretability.
assert (  # noqa: S101
    abs(
        _WEIGHT_CODE
        + _WEIGHT_STRUCTURED
        + _WEIGHT_IDENTIFIERS
        + _WEIGHT_NUMERIC
        + _WEIGHT_LINE_STRUCTURE
        - 1.0
    )
    < _WEIGHT_SUM_TOLERANCE
), "Density signal weights must sum to 1.0"

# ── Compiled patterns ───────────────────────────────────────────

_CODE_PATTERNS = re.compile(
    r"(?:"
    r"def\s+\w+\s*\(|"  # Python function
    r"class\s+\w+|"  # class definition
    r"import\s+\w+|"  # import statement
    r"from\s+\w+\s+import|"  # from-import
    r"return\s+|"  # return statement
    r"if\s+.*:|"  # if statement
    r"for\s+\w+\s+in\s+|"  # for loop
    r"\w+\s*=\s*[\[{(]|"  # assignment to collection
    r"[{}\[\]();]"  # structural delimiters
    r")",
)

_STRUCTURED_PATTERNS = re.compile(
    r'(?:"[\w-]+":\s*|'  # JSON keys
    r"^\s*[\w-]+:\s+\S|"  # YAML key-value
    r"</?[\w-]+[>\s]|"  # XML/HTML tags
    r"^\s*-\s+\S)",  # YAML list items
    re.MULTILINE,
)

_IDENTIFIER_PATTERNS = re.compile(
    r"(?:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|"  # UUID
    r"\b[0-9a-f]{32,}\b|"  # hex hash
    r"https?://\S+|"  # URL
    r"\b[a-z]+(?:_[a-z]+){2,}\b|"  # snake_case (3+ parts)
    r"\b[a-z]+(?:[A-Z][a-z]+){2,}\b"  # camelCase (3+ parts)
    r")",
    re.IGNORECASE,
)

_NUMERIC_PATTERNS = re.compile(
    r"(?:"
    r"\bv?\d+\.\d+\.\d+\b|"  # version numbers
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b|"  # IP addresses
    r"\b\d{4}-\d{2}-\d{2}\b|"  # ISO dates
    r"\b\d{5,}\b"  # long numeric sequences
    r")",
)

_FALLBACK_SCORE: Final[float] = 0.0
_MIN_LINES_FOR_STRUCTURE: Final[int] = 2

# Per-signal scaling factors. Each signal raw-counts pattern hits and
# divides by an input-size proxy (words / lines); the factor below
# converts the resulting density into a 0.0-1.0 score, saturated at 1.0.
# Calibrated against the corpus described in
# `docs/design/memory.md` § "Density classification".
_CODE_PATTERN_SCALE: Final[float] = 5.0
_STRUCTURED_DATA_SCALE: Final[float] = 2.0
_IDENTIFIER_DENSITY_SCALE: Final[float] = 8.0
_NUMERIC_DENSITY_SCALE: Final[float] = 10.0

# Line-structure heuristics: short average line length plus many lines
# implies code-like layout. Tuned for typical 80-column source files.
_SHORT_LINE_LENGTH_THRESHOLD: Final[float] = 80.0
_MULTILINE_DENSITY_SCALE: Final[float] = 10.0

# Default density threshold above which content is classified DENSE.
_DEFAULT_DENSE_THRESHOLD: Final[float] = 0.5


def _code_pattern_score(text: str) -> float:
    """Score code-like patterns in the text (0.0 to 1.0).

    Returns:
        Result of type ``float``.
    """
    matches = len(_CODE_PATTERNS.findall(text))
    words = max(len(text.split()), 1)
    ratio = matches / words
    return min(ratio * _CODE_PATTERN_SCALE, 1.0)


def _structured_data_score(text: str) -> float:
    """Score structured data markers in the text (0.0 to 1.0).

    Returns:
        Result of type ``float``.
    """
    matches = len(_STRUCTURED_PATTERNS.findall(text))
    lines = max(len(text.splitlines()), 1)
    ratio = matches / lines
    return min(ratio * _STRUCTURED_DATA_SCALE, 1.0)


def _identifier_density_score(text: str) -> float:
    """Score identifier-like tokens in the text (0.0 to 1.0).

    Returns:
        Result of type ``float``.
    """
    matches = len(_IDENTIFIER_PATTERNS.findall(text))
    words = max(len(text.split()), 1)
    ratio = matches / words
    return min(ratio * _IDENTIFIER_DENSITY_SCALE, 1.0)


def _numeric_density_score(text: str) -> float:
    """Score numeric patterns in the text (0.0 to 1.0).

    Returns:
        Result of type ``float``.
    """
    matches = len(_NUMERIC_PATTERNS.findall(text))
    words = max(len(text.split()), 1)
    ratio = matches / words
    return min(ratio * _NUMERIC_DENSITY_SCALE, 1.0)


def _line_structure_score(text: str) -> float:
    """Score line structure (short lines + many lines = dense).

    Returns:
        Result of type ``float``.
    """
    lines = text.splitlines()
    if len(lines) < _MIN_LINES_FOR_STRUCTURE:
        return _FALLBACK_SCORE
    avg_len = sum(len(line) for line in lines) / len(lines)
    # Short average line length (below threshold) + multiple lines = code-like
    short_line_score = max(0.0, 1.0 - avg_len / _SHORT_LINE_LENGTH_THRESHOLD)
    multi_line_score = min(len(lines) / _MULTILINE_DENSITY_SCALE, 1.0)
    return short_line_score * multi_line_score


class DensityClassifier:
    """Heuristic content density classifier.

    Classifies text as SPARSE or DENSE based on structural signals:
    code patterns, structured data markers, identifier density,
    numeric density, and line structure.

    Args:
        dense_threshold: Score threshold for DENSE classification
            (0.0-1.0).  Lower values classify more content as dense.

    Raises:
        ValueError: If ``dense_threshold`` is outside [0.0, 1.0].
    """

    def __init__(self, *, dense_threshold: float = _DEFAULT_DENSE_THRESHOLD) -> None:
        if not 0.0 <= dense_threshold <= 1.0:
            msg = f"dense_threshold must be in [0.0, 1.0], got {dense_threshold}"
            raise ValueError(msg)
        self._threshold = dense_threshold

    def classify(self, content: str) -> ContentDensity:
        """Classify content density.

        Args:
            content: Text to classify.

        Returns:
            DENSE if score >= threshold, SPARSE otherwise.
        """
        score = (
            _WEIGHT_CODE * _code_pattern_score(content)
            + _WEIGHT_STRUCTURED * _structured_data_score(content)
            + _WEIGHT_IDENTIFIERS * _identifier_density_score(content)
            + _WEIGHT_NUMERIC * _numeric_density_score(content)
            + _WEIGHT_LINE_STRUCTURE * _line_structure_score(content)
        )
        return (
            ContentDensity.DENSE if score >= self._threshold else ContentDensity.SPARSE
        )

    def classify_batch(
        self,
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[tuple[MemoryEntry, ContentDensity], ...]:
        """Classify density for a batch of memory entries.

        Args:
            entries: Memory entries to classify.

        Returns:
            Tuple of (entry, density) pairs in input order.
        """
        results = tuple((entry, self.classify(entry.content)) for entry in entries)

        if results:
            dense_count = sum(1 for _, d in results if d == ContentDensity.DENSE)
            logger.debug(
                DENSITY_CLASSIFICATION_COMPLETE,
                entry_count=len(results),
                dense_count=dense_count,
                sparse_count=len(results) - dense_count,
            )

        return results
