"""Extractive preservation for dense memory content.

For dense content (code, structured data, identifiers), summarization
is catastrophically lossy.  Instead, this module extracts verbatim
key facts and structural anchors (start/mid/end) to preserve the
most important information.
"""

import re
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import (
    DUAL_MODE_EXTRACTIVE_PRESERVED,
)

logger = get_logger(__name__)
_DEFAULT_MAX_FACTS: Final[int] = 20
_DEFAULT_ANCHOR_LENGTH: Final[int] = 150

# ── Extraction patterns ─────────────────────────────────────────

_URL_PATTERN = re.compile(r"https?://\S+")

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_HEX_HASH_PATTERN = re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE)

# Require leading 'v' OR exclude 4-octet dotted quads (IPv4).
_VERSION_PATTERN = re.compile(
    r"\bv\d+\.\d+\.\d+(?:\.\d+)?\b"
    r"|\b\d+\.\d+\.\d+\b(?!\.\d)"
)

_KEY_VALUE_PATTERN = re.compile(
    r"^\s*([\w.-]+\s*[:=]\s*.*)$",
    re.MULTILINE,
)


def _extract_urls(text: str) -> list[str]:
    """Extract URLs from text.

    Returns:
        List of ``str``.
    """
    return _URL_PATTERN.findall(text)


def _extract_identifiers(text: str) -> list[str]:
    """Extract UUIDs and hex hashes from text.

    Returns:
        List of ``str``.
    """
    uuids = _UUID_PATTERN.findall(text)
    hashes = _HEX_HASH_PATTERN.findall(text)
    return uuids + hashes


def _extract_versions(text: str) -> list[str]:
    """Extract version numbers from text.

    Returns:
        List of ``str``.
    """
    return _VERSION_PATTERN.findall(text)


def _extract_key_values(text: str) -> list[str]:
    """Extract key-value pairs from text, preserving original form.

    Returns:
        List of ``str``.
    """
    return [m.strip() for m in _KEY_VALUE_PATTERN.findall(text)]


def _build_anchors(
    text: str,
    anchor_length: int,
) -> tuple[str, str, str]:
    """Extract start, mid, and end anchor snippets.

    Args:
        text: Source text.
        anchor_length: Maximum characters per anchor.

    Returns:
        Tuple of (start, mid, end) anchor strings.
    """
    text_len = len(text)

    if text_len <= anchor_length:
        return text, "", ""

    start = text[:anchor_length] + "..."

    mid_offset = max(0, (text_len - anchor_length) // 2)
    mid = text[mid_offset : mid_offset + anchor_length]
    if mid_offset > 0:
        mid = "..." + mid
    if mid_offset + anchor_length < text_len:
        mid += "..."

    end = "..." + text[-anchor_length:]

    return start, mid, end


class ExtractivePreserver:
    """Extracts key facts and structural anchors from dense content.

    For dense content (code, structured data, IDs), summarization is
    catastrophically lossy.  Instead, this preserver extracts verbatim
    key facts (identifiers, URLs, version numbers, key-value pairs)
    and structural anchors (start/mid/end snippets of the original).

    Args:
        max_facts: Maximum number of key facts to extract.
        anchor_length: Character length of each anchor snippet.

    Raises:
        ValueError: If ``max_facts`` or ``anchor_length`` is < 1.
    """

    def __init__(
        self,
        *,
        max_facts: int = _DEFAULT_MAX_FACTS,
        anchor_length: int = _DEFAULT_ANCHOR_LENGTH,
    ) -> None:
        if max_facts < 1:
            msg = f"max_facts must be >= 1, got {max_facts}"
            raise ValueError(msg)
        if anchor_length < 1:
            msg = f"anchor_length must be >= 1, got {anchor_length}"
            raise ValueError(msg)
        self._max_facts = max_facts
        self._anchor_length = anchor_length

    def extract(self, content: str) -> str:
        """Extract key facts and anchors from dense content.

        Args:
            content: The dense text to extract from.

        Returns:
            Structured text block with extracted facts and anchors.
        """
        # Collect all facts, deduplicated, order-preserving
        all_facts: list[str] = []
        seen: set[str] = set()

        for fact in (
            *_extract_urls(content),
            *_extract_identifiers(content),
            *_extract_versions(content),
            *_extract_key_values(content),
        ):
            if fact not in seen:
                seen.add(fact)
                all_facts.append(fact)

        facts = all_facts[: self._max_facts]
        start, mid, end = _build_anchors(content, self._anchor_length)

        lines = ["[Extractive preservation]"]
        if facts:
            lines.append("Key facts:")
            lines.extend(f"- {fact}" for fact in facts)
        lines.append(f"[START] {start}")
        if mid:
            lines.append(f"[MID] {mid}")
        if end:
            lines.append(f"[END] {end}")

        result = "\n".join(lines)

        logger.debug(
            DUAL_MODE_EXTRACTIVE_PRESERVED,
            content_length=len(content),
            fact_count=len(facts),
            anchor_length=self._anchor_length,
        )

        return result
