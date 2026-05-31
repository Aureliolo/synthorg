"""Deterministic chunker for a :class:`BrainEntry`.

Unlike a living document (a block list), a brain entry is a small, fixed set of
text fields: a kind/status/title header, the rationale (the "why"), and the
free-text fields of the discriminated payload. The chunker renders those into an
ordered sequence of text segments, splits any oversized segment at sentence
boundaries (the rationale can be up to several thousand characters), and merges
adjacent segments up to a target size.

The chunker is deterministic: the same entry plus ``project_id`` always yields
the same chunks in the same order, so a re-index replaces prior chunks exactly
without phantom duplicates. Chunk size is approximated in tokens via a fixed
characters-per-token proxy (no tokeniser dependency); the proxy and the size
budgets live in :mod:`synthorg.project_brain.constants`.
"""

import re

from synthorg.core.types import NotBlankStr
from synthorg.project_brain.constants import (
    BRAIN_CHAR_PER_TOKEN_PROXY,
    BRAIN_CHUNK_MAX_TOKENS,
    BRAIN_CHUNK_TARGET_TOKENS,
    BRAIN_ENTRY_TAG_PREFIX,
    BRAIN_KIND_TAG_PREFIX,
    BRAIN_PROJECT_TAG_PREFIX,
)
from synthorg.project_brain.models import (
    BlockerPayload,
    BrainChunk,
    BrainEntry,
    BrainPayloadValue,
    DecisionPayload,
    DependencyPayload,
    OpenQuestionPayload,
    PlanRevisionPayload,
    RiskPayload,
)

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class BrainChunker:
    """Deterministic field-aware chunker for project-brain entries."""

    __slots__ = ("_max_tokens", "_target_tokens")

    def __init__(
        self,
        *,
        target_tokens: int = BRAIN_CHUNK_TARGET_TOKENS,
        max_tokens: int = BRAIN_CHUNK_MAX_TOKENS,
    ) -> None:
        """Configure chunk-size budgets.

        Args:
            target_tokens: Soft target; adjacent segments merge up to this.
            max_tokens: Hard cap; oversized segments split at sentence
                boundaries down to this bound.

        Raises:
            ValueError: When ``max_tokens`` is less than ``target_tokens``.
        """
        if max_tokens < target_tokens:
            msg = (
                f"max_tokens ({max_tokens}) must be >= target_tokens ({target_tokens})"
            )
            raise ValueError(msg)
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens

    def chunk(
        self,
        *,
        project_id: NotBlankStr,
        entry: BrainEntry,
    ) -> tuple[BrainChunk, ...]:
        """Decompose *entry* into a deterministic sequence of chunks.

        Args:
            project_id: Owning project; embedded in each chunk's tags via the
                ``project:<id>`` prefix.
            entry: Source brain entry revision.

        Returns:
            Ordered tuple of :class:`BrainChunk` instances. Always at least one
            chunk: even a minimal entry carries its header segment.
        """
        base_tags = self._base_tags(project_id=project_id, entry=entry)
        units: list[str] = []
        for segment in _entry_segments(entry):
            units.extend(self._split_to_max(segment))
        chunk_texts = self._merge(units, budget=self._target_tokens)
        return tuple(
            BrainChunk(
                project_id=project_id,
                entry_id=entry.entry_id,
                entry_kind=entry.entry_kind,
                chunk_index=index,
                text=NotBlankStr(text or " "),
                tags=base_tags,
            )
            for index, text in enumerate(chunk_texts)
        )

    def _token_count(self, text: str) -> int:
        """Approximate token count via the chars-per-token proxy.

        Returns:
            ``0`` for empty text, otherwise at least ``1`` token scaled by the
            chars-per-token proxy.
        """
        if not text:
            return 0
        return max(1, len(text) // BRAIN_CHAR_PER_TOKEN_PROXY)

    def _split_to_max(self, text: str) -> list[str]:
        """Split *text* into pieces each within the hard ``max_tokens`` bound.

        Splits at sentence boundaries first; a single sentence longer than the
        bound is hard-split by characters so the routine always terminates.

        Returns:
            One or more pieces, each at most ``max_tokens`` in proxy units.
        """
        if self._token_count(text) <= self._max_tokens:
            return [text]
        max_chars = self._max_tokens * BRAIN_CHAR_PER_TOKEN_PROXY
        pieces: list[str] = []
        for sentence in _SENTENCE_BOUNDARY.split(text):
            if not sentence:
                continue
            start = 0
            while start < len(sentence):
                pieces.append(sentence[start : start + max_chars])
                start += max_chars
        return self._merge(pieces, budget=self._max_tokens)

    def _merge(self, units: list[str], *, budget: int) -> list[str]:
        """Greedily merge adjacent *units* up to *budget* proxy tokens.

        A unit that already exceeds *budget* on its own becomes its own chunk
        (it was split to ``max_tokens`` upstream and cannot be divided further
        without crossing a sentence boundary mid-word).

        Returns:
            The merged chunk texts in input order.
        """
        chunks: list[str] = []
        current = ""
        for unit in units:
            if not unit:
                continue
            candidate = f"{current}\n\n{unit}" if current else unit
            if current and self._token_count(candidate) > budget:
                chunks.append(current)
                current = unit
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _base_tags(
        *,
        project_id: NotBlankStr,
        entry: BrainEntry,
    ) -> tuple[NotBlankStr, ...]:
        """Tags carried by every chunk: project, entry, kind, then entry tags.

        Returns:
            The base tag tuple (project, brain_entry, brain_kind prefixed)
            followed by the entry's own tags.
        """
        return (
            NotBlankStr(f"{BRAIN_PROJECT_TAG_PREFIX}{project_id}"),
            NotBlankStr(f"{BRAIN_ENTRY_TAG_PREFIX}{entry.entry_id}"),
            NotBlankStr(f"{BRAIN_KIND_TAG_PREFIX}{entry.entry_kind.value}"),
            *entry.tags,
        )


def _entry_segments(entry: BrainEntry) -> list[str]:
    """Render *entry* to an ordered list of embeddable text segments.

    The first segment is a self-describing header (kind, status, title) so a
    retrieved chunk identifies its source without a repository lookup. The
    rationale follows, then the payload's free-text fields.

    Returns:
        Non-empty text segments in deterministic order.
    """
    segments = [
        f"[{entry.entry_kind.value}/{entry.status.value}] {entry.title}",
        entry.rationale,
        *_payload_segments(entry.payload),
    ]
    return [segment for segment in segments if segment.strip()]


def _payload_segments(payload: BrainPayloadValue) -> list[str]:
    """Render a discriminated payload's free-text fields to segments.

    Returns:
        The payload-specific text segments (omitting empty optional fields).

    Raises:
        ValueError: When *payload* is an unhandled kind (unreachable; the
            union is exhaustive and mypy proves it).
    """
    if isinstance(payload, DecisionPayload):
        segments = [f"Decision: {payload.decision_outcome}"]
        if payload.alternatives:
            segments.append("Alternatives: " + "; ".join(payload.alternatives))
        return segments
    if isinstance(payload, OpenQuestionPayload):
        return [f"Answer: {payload.answer}"] if payload.answer else []
    if isinstance(payload, BlockerPayload):
        segments = [f"Severity: {payload.severity.value}"]
        if payload.resolution:
            segments.append(f"Resolution: {payload.resolution}")
        return segments
    if isinstance(payload, RiskPayload):
        segments = [
            f"Likelihood: {payload.likelihood.value}; impact: {payload.impact.value}"
        ]
        if payload.mitigation:
            segments.append(f"Mitigation: {payload.mitigation}")
        return segments
    if isinstance(payload, DependencyPayload):
        return [f"Depends on {payload.depends_on} ({payload.dependency_kind.value})"]
    if isinstance(payload, PlanRevisionPayload):
        return [f"Plan: {payload.summary}"]
    # Exhaustive over the BrainPayload union; mypy proves this unreachable.
    msg = f"Unhandled payload kind: {type(payload).__name__}"  # type: ignore[unreachable]
    raise ValueError(msg)
