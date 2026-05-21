"""Unit tests for :mod:`synthorg.knowledge.freshness`."""

import pytest

from synthorg.core.enums import ContentKind
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.freshness import ChunkDiff, diff_chunks, make_chunk_id
from synthorg.knowledge.models import KnowledgeChunk, WebLocator
from synthorg.versioning.hashing import compute_text_hash

pytestmark = pytest.mark.unit


def _chunk(index: int, text: str, source_id: str = "src-1") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=make_chunk_id(NotBlankStr(source_id), index),
        source_id=NotBlankStr(source_id),
        content_kind=ContentKind.DOCUMENT,
        chunk_index=index,
        text=text,
        content_hash=compute_text_hash(text),
        locator=WebLocator(
            url=NotBlankStr("https://x.test"), char_start=0, char_end=len(text)
        ),
    )


class TestMakeChunkId:
    def test_positional_and_deterministic(self) -> None:
        assert make_chunk_id(NotBlankStr("src-1"), 0) == "src-1#0"
        assert make_chunk_id(NotBlankStr("src-1"), 0) == make_chunk_id(
            NotBlankStr("src-1"), 0
        )


class TestDiffChunks:
    def test_all_new_when_no_existing(self) -> None:
        chunks = (_chunk(0, "alpha"), _chunk(1, "beta"))
        diff = diff_chunks(existing_hashes={}, chunks=chunks)
        assert len(diff.to_embed) == 2
        assert diff.unchanged == ()
        assert diff.removed_ids == ()
        assert diff.is_noop is False

    def test_unchanged_skipped(self) -> None:
        chunks = (_chunk(0, "alpha"), _chunk(1, "beta"))
        existing = {c.chunk_id: c.content_hash for c in chunks}
        diff = diff_chunks(existing_hashes=existing, chunks=chunks)
        assert diff.to_embed == ()
        assert len(diff.unchanged) == 2
        assert diff.is_noop is True

    def test_only_changed_reembedded(self) -> None:
        original = (_chunk(0, "alpha"), _chunk(1, "beta"))
        existing = {c.chunk_id: c.content_hash for c in original}
        edited = (_chunk(0, "alpha"), _chunk(1, "beta CHANGED"))
        diff = diff_chunks(existing_hashes=existing, chunks=edited)
        assert {c.chunk_id for c in diff.to_embed} == {"src-1#1"}
        assert {c.chunk_id for c in diff.unchanged} == {"src-1#0"}
        assert diff.removed_ids == ()

    def test_removed_chunk_detected(self) -> None:
        original = (_chunk(0, "alpha"), _chunk(1, "beta"))
        existing = {c.chunk_id: c.content_hash for c in original}
        shrunk = (_chunk(0, "alpha"),)
        diff = diff_chunks(existing_hashes=existing, chunks=shrunk)
        assert diff.removed_ids == ("src-1#1",)
        assert len(diff.unchanged) == 1

    def test_empty_diff_is_noop(self) -> None:
        assert diff_chunks(existing_hashes={}, chunks=()) == ChunkDiff()
