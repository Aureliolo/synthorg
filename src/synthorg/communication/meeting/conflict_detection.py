"""Conflict detection strategies for meeting protocols.

Implements multiple conflict detection strategies for the
StructuredPhasesProtocol to determine whether discussion is needed
based on leader responses during conflict-check phase.

Strategies include:
- Keyword matching (default, simple and fast)
- Structured JSON comparison (zero-cost, deterministic)
- Judgment parsing from LLM responses
- Hybrid approaches combining multiple strategies
"""

import re

from synthorg.communication.meeting.embedder import (
    TextEmbedder,
    cosine_similarity,
)
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import (
    STRATEGY_CONFLICT_PARSE_FAILED,
)

CONFLICT_PARSE_FAILED = STRATEGY_CONFLICT_PARSE_FAILED

logger = get_logger(__name__)

# Default cosine similarity threshold below which two leader positions
# are considered conflicting.  0.7 is the empirical "clearly distinct"
# point for sentence-transformer embeddings on chat-like text; below
# this the responses diverge enough that downstream discussion adds
# value, above this they agree closely enough to skip the discussion
# phase.  Shared by EmbeddingSimilarityDetector and HybridDetector.
_DEFAULT_SIMILARITY_THRESHOLD: float = 0.7


_MIN_POSITIONS_FOR_CONFLICT: int = 2

# Identity/metadata keys to skip during structured field comparison.
# These always differ between agents and are not substantive decision fields.
_IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "agent_id",
        "role",
        "timestamp",
        "created_at",
        "updated_at",
        "metadata",
        "id",
        "author",
        "participant_id",
    }
)


def _extract_json_object(text: str) -> dict[str, object] | None:
    """Extract a JSON object from text via the shared LLM JSON helper.

    Returns:
        The parsed JSON object, or ``None`` if none was found.
    """
    return extract_json_from_llm_response(text)


class KeywordConflictDetector:
    """Default conflict detector using keyword matching.

    Looks for the string "CONFLICTS: YES" (case-insensitive) in
    the agent response. Allows flexible whitespace around the colon.
    This is the simplest approach and works well when agents are
    prompted to include this marker.
    """

    def detect(self, response_content: str) -> bool:
        """Detect conflicts via keyword matching.

        Looks for "CONFLICTS" followed by optional whitespace, colon,
        optional whitespace, and "YES" (all case-insensitive).

        Args:
            response_content: The conflict-check agent response text.

        Returns:
            True if conflict marker is found, False otherwise.
        """
        content_upper = response_content.upper()
        # Match "CONFLICTS" with optional space, ":", optional space, "YES"
        return bool(re.search(r"CONFLICTS\s*:\s*YES", content_upper))


class StructuredComparisonDetector:
    """Compare structured fields in agent responses.

    Expects response_content to contain JSON with a "positions" or
    "position" field containing agent positions. Parses all positions,
    compares them field-by-field. Reports conflict if any top-level
    field values differ between positions.

    Zero cost, deterministic, and does not require external calls.
    """

    def detect(self, response_content: str) -> bool:
        """Detect conflicts via structured JSON field comparison.

        Attempts to extract JSON from response_content, then compares
        all agent positions field-by-field. If any top-level field
        value differs between any two positions, returns True.

        Args:
            response_content: The conflict-check agent response (should contain JSON).

        Returns:
            True if conflicts (differing field values) detected, False otherwise.
        """
        try:
            # Try to extract JSON from response
            parsed = self._extract_json(response_content)
            if parsed is None:
                return False

            positions = self._get_positions(parsed)
            if not positions or len(positions) < _MIN_POSITIONS_FOR_CONFLICT:
                return False

            # Compare all pairs of positions field-by-field
            return self._has_field_conflicts(positions)
        except TypeError, ValueError:
            logger.debug(
                CONFLICT_PARSE_FAILED,
                detector="StructuredComparisonDetector",
            )
            return False

    def _extract_json(self, text: str) -> dict[str, object] | None:
        """Extract JSON object from text.

        Returns:
            The parsed JSON object, or ``None`` if none was found.
        """
        return _extract_json_object(text)

    def _get_positions(
        self,
        data: dict[str, object],
    ) -> list[dict[str, object]]:
        """Extract positions from parsed JSON.

        Looks for "positions" (plural) or "position" (singular) fields.

        Returns:
            The list of position dicts (empty if none are present).
        """
        if "positions" in data:
            positions = data["positions"]
            if isinstance(positions, list):
                return [p for p in positions if isinstance(p, dict)]

        if "position" in data:
            position = data["position"]
            if isinstance(position, dict):
                return [position]

        return []

    def _has_field_conflicts(
        self,
        positions: list[dict[str, object]],
    ) -> bool:
        """Check if any top-level field differs between positions.

        Compares all pairs of positions.

        Returns:
            ``True`` if any position differs from another on any
            top-level field.
        """
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                pos_a = positions[i]
                pos_b = positions[j]

                # Collect substantive field keys (skip identity/metadata)
                all_keys = (set(pos_a.keys()) | set(pos_b.keys())) - _IDENTITY_KEYS

                for key in all_keys:
                    val_a = pos_a.get(key)
                    val_b = pos_b.get(key)

                    # If values differ, conflict detected
                    if val_a != val_b:
                        return True

        return False


class LlmJudgeDetector:
    """Parse conflict judgment from LLM-structured responses.

    Expects the leader's response to contain a structured judgment,
    typically in JSON format with a "conflicts" or "judgment" field.

    The leader is prompted separately (via orchestrator) to provide
    structured judgment; this detector just parses the result.
    """

    def detect(self, response_content: str) -> bool:
        """Detect conflicts via LLM judgment parsing.

        Looks for JSON with a "conflicts" boolean field, or parses
        "JUDGE: CONFLICT" / "JUDGE: NO_CONFLICT" markers.
        Falls back to keyword detection if structured format not found.

        Args:
            response_content: The leader's structured judgment response.

        Returns:
            True if judgment indicates conflicts, False otherwise.
        """
        # Try JSON parsing first
        try:
            parsed = self._extract_json(response_content)
            if parsed:
                # Check for "conflicts" field (boolean)
                if "conflicts" in parsed:
                    conflicts = parsed["conflicts"]
                    if isinstance(conflicts, bool):
                        return conflicts

                # Check for "judgment" field
                if "judgment" in parsed:
                    judgment = parsed["judgment"]
                    if isinstance(judgment, str):
                        normalized = judgment.lower()
                        # Negation-aware check: "no conflict(s)",
                        # "not a conflict", "without conflict", etc.
                        if re.search(
                            r"\b(no|not|none|without|zero)\b.*\bconflicts?\b",
                            normalized,
                        ):
                            return False
                        return bool(re.search(r"\bconflicts?\b", normalized))
        except TypeError, ValueError:
            logger.debug(
                CONFLICT_PARSE_FAILED,
                detector="LlmJudgeDetector",
            )

        # Fallback to keyword markers (whitespace-tolerant)
        if re.search(
            r"JUDGE\s*[:\-]\s*NO[_\s]*CONFLICT",
            response_content,
            re.IGNORECASE,
        ):
            return False
        if re.search(
            r"JUDGE\s*[:\-]\s*CONFLICT",
            response_content,
            re.IGNORECASE,
        ):
            return True

        # Fallback to whitespace-tolerant keyword detection
        return KeywordConflictDetector().detect(response_content)

    def _extract_json(self, text: str) -> dict[str, object] | None:
        """Extract JSON object from text.

        Returns:
            The parsed JSON object, or ``None`` if none was found.
        """
        return _extract_json_object(text)


def _position_texts(response_content: str) -> tuple[str, ...]:
    """Extract one comparable text per agent position from the response.

    Parses the JSON ``positions`` / ``position`` payload and renders each
    position's substantive (non-identity) fields into a stable text so
    divergent stances embed to divergent vectors.

    Returns:
        One text per position; empty when no positions are present.
    """
    parsed = _extract_json_object(response_content)
    if parsed is None:
        return ()
    raw = parsed.get("positions")
    positions: list[dict[str, object]]
    if isinstance(raw, list):
        positions = [p for p in raw if isinstance(p, dict)]
    else:
        single = parsed.get("position")
        positions = [single] if isinstance(single, dict) else []
    texts: list[str] = []
    for position in positions:
        parts = [
            f"{key}: {value}"
            for key, value in sorted(position.items())
            if key not in _IDENTITY_KEYS
        ]
        texts.append(" ".join(parts))
    return tuple(texts)


class EmbeddingSimilarityDetector:
    """Embedding-based similarity detection over agent positions.

    Extracts the agent positions from the conflict-check response, embeds
    each via the injected :class:`TextEmbedder`, and flags a conflict
    when any pair of positions is dissimilar enough (cosine similarity
    below ``similarity_threshold``) that downstream discussion is
    worthwhile.

    Args:
        embedder: The text-embedding backend.
        similarity_threshold: Cosine similarity below which two positions
            are treated as conflicting.
    """

    def __init__(
        self,
        *,
        embedder: TextEmbedder,
        similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._embedder = embedder
        self.similarity_threshold = similarity_threshold

    def detect(self, response_content: str) -> bool:
        """Detect conflicts via pairwise embedding similarity.

        Args:
            response_content: The conflict-check response (JSON positions).

        Returns:
            ``True`` when at least one pair of positions is dissimilar
            below the threshold; ``False`` when fewer than two positions
            are present or all pairs agree.
        """
        texts = _position_texts(response_content)
        if len(texts) < _MIN_POSITIONS_FOR_CONFLICT:
            return False
        vectors = [self._embedder.embed(text) for text in texts]
        threshold = self.similarity_threshold
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                if cosine_similarity(vectors[i], vectors[j]) < threshold:
                    return True
        return False


class HybridDetector:
    """Embedding first, keyword fallback.

    Consults the embedding detector first; when it finds no conflict the
    keyword detector provides a deterministic second opinion (so an
    explicit ``CONFLICTS: YES`` marker is honoured even when the
    positions embed closely).

    Args:
        embedder: The text-embedding backend for the embedding leg.
        similarity_threshold: Cosine similarity threshold for the
            embedding leg.
    """

    def __init__(
        self,
        *,
        embedder: TextEmbedder,
        similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self.embedding_detector = EmbeddingSimilarityDetector(
            embedder=embedder,
            similarity_threshold=similarity_threshold,
        )
        self.keyword_detector = KeywordConflictDetector()

    def detect(self, response_content: str) -> bool:
        """Detect conflicts using embedding first, then keyword.

        Args:
            response_content: The conflict-check response.

        Returns:
            True if conflicts detected via either strategy.
        """
        if self.embedding_detector.detect(response_content):
            return True
        return self.keyword_detector.detect(response_content)


class AutoDetector:
    """Selects detection strategy based on response content format.

    Inspects the response to determine its format:
    - If response is valid JSON with "position" or "positions" field:
      uses StructuredComparisonDetector
    - Otherwise: uses KeywordConflictDetector (default fallback)

    This allows responses to choose their optimal detection method
    implicitly based on content structure.
    """

    def __init__(self) -> None:
        """Initialize with both detector strategies."""
        self.structured_detector = StructuredComparisonDetector()
        self.keyword_detector = KeywordConflictDetector()

    def detect(self, response_content: str) -> bool:
        """Detect conflicts using format-aware strategy selection.

        Checks if response contains JSON with "position" or "positions"
        field. If so, uses StructuredComparisonDetector. Otherwise uses
        KeywordConflictDetector.

        Args:
            response_content: The conflict-check response.

        Returns:
            True if conflicts detected via selected strategy.
        """
        # Try to detect if response is structured JSON
        if self._is_structured_json(response_content):
            return self.structured_detector.detect(response_content)

        # Fall back to keyword detection
        return self.keyword_detector.detect(response_content)

    def _is_structured_json(self, text: str) -> bool:
        """Check if text is JSON with position field.

        Returns:
            ``True`` if the text parses as JSON carrying a
            ``position``/``positions`` field.
        """
        parsed = _extract_json_object(text)
        if parsed is not None:
            return "position" in parsed or "positions" in parsed
        return False
