"""Unit tests for :mod:`synthorg.docs_engine.serializer`.

The serializer is the canonical on-disk encoder for living docs. Its
two load-bearing guarantees are:

1. **Determinism**: the same input always produces the same bytes (no
   key-order or whitespace variance), so git diffs stay localised and
   re-writes that change nothing produce no diff.
2. **Round-trip**: ``deserialize_doc(serialize_doc(doc)) == doc``.

Both are validated here. The error paths (invalid UTF-8, invalid JSON,
schema mismatch) raise :class:`DocValidationError` rather than letting
internal exceptions leak.
"""

from datetime import UTC, datetime

import pytest

from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.errors import DocValidationError
from synthorg.docs_engine.models import (
    BulletListBlock,
    DecisionBlock,
    HeadingBlock,
    LivingDocument,
    ProseBlock,
)
from synthorg.docs_engine.serializer import deserialize_doc, serialize_doc

pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def _doc() -> LivingDocument:
    return LivingDocument(
        slug="q2-status",
        title="Q2 status report",
        doc_type=DocType.STATUS_REPORT,
        author_agent_id="agent_alice",
        tags=("checkout", "q2"),
        body=(
            HeadingBlock(level=2, text="Summary"),
            ProseBlock(text="Checkout funnel improved by 5% over April."),
            BulletListBlock(items=("A/B test concluded", "shipped fix")),
            DecisionBlock(
                decision="Hold on funnel rewrite",
                rationale="Existing A/B test still ramping",
            ),
        ),
        created_at=_ts(),
        updated_at=_ts(),
    )


class TestRoundTrip:
    def test_round_trip_preserves_doc(self) -> None:
        doc = _doc()
        raw = serialize_doc(doc)
        restored = deserialize_doc(raw)
        assert restored == doc

    def test_round_trip_preserves_block_ids(self) -> None:
        doc = _doc()
        raw = serialize_doc(doc)
        restored = deserialize_doc(raw)
        assert tuple(b.block_id for b in restored.body) == tuple(
            b.block_id for b in doc.body
        )


class TestDeterminism:
    def test_repeated_serialize_yields_identical_bytes(self) -> None:
        doc = _doc()
        first = serialize_doc(doc)
        second = serialize_doc(doc)
        assert first == second

    def test_trailing_newline(self) -> None:
        raw = serialize_doc(_doc())
        assert raw.endswith(b"\n")

    def test_keys_sorted(self) -> None:
        raw = serialize_doc(_doc())
        text = raw.decode("utf-8")
        # The top-level JSON object's keys appear in sorted order; check
        # by finding the index of a few canonical keys and asserting the
        # order matches alphabetical.
        author_idx = text.index('"author_agent_id"')
        title_idx = text.index('"title"')
        updated_idx = text.index('"updated_at"')
        assert author_idx < title_idx < updated_idx


class TestErrorPaths:
    def test_invalid_utf8_raises_validation_error(self) -> None:
        with pytest.raises(DocValidationError):
            deserialize_doc(b"\xff\xfe\x00")

    def test_invalid_json_raises_validation_error(self) -> None:
        with pytest.raises(DocValidationError):
            deserialize_doc(b"not json at all")

    def test_schema_mismatch_raises_validation_error(self) -> None:
        with pytest.raises(DocValidationError):
            deserialize_doc(b'{"slug": "x"}')
