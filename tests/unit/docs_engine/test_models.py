"""Unit tests for :mod:`synthorg.docs_engine.models`.

Asserts the structural invariants the rest of the engine relies on:
frozen + extra-forbid, discriminated union resolution, slug + title +
author always required, body order preserved, bounded payload sizes.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.models import (
    BulletListBlock,
    CodeBlock,
    DecisionBlock,
    HeadingBlock,
    LinkBlock,
    LivingDocument,
    MetricBlock,
    ProseBlock,
)

pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)


def _make_doc(**overrides: object) -> LivingDocument:
    fields: dict[str, object] = {
        "slug": "q2-status",
        "title": "Q2 status report",
        "doc_type": DocType.STATUS_REPORT,
        "author_agent_id": "agent_alice",
        "body": (
            HeadingBlock(level=2, text="Summary"),
            ProseBlock(text="Checkout funnel improved by 5%."),
        ),
        "created_at": _ts(),
        "updated_at": _ts(),
    }
    fields.update(overrides)
    return LivingDocument(**fields)  # type: ignore[arg-type]


class TestLivingDocument:
    """Structural invariants for the top-level document model."""

    def test_minimal_doc_constructs(self) -> None:
        doc = _make_doc()
        assert doc.slug == "q2-status"
        assert doc.doc_type is DocType.STATUS_REPORT
        assert len(doc.body) == 2

    def test_model_is_frozen(self) -> None:
        doc = _make_doc()
        with pytest.raises(ValidationError):
            doc.title = "Mutated"  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            LivingDocument(
                slug="x",
                title="t",
                doc_type=DocType.STATUS_REPORT,
                author_agent_id="a",
                created_at=_ts(),
                updated_at=_ts(),
                extra_field="boom",  # type: ignore[call-arg]
            )

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_doc(title="   ")

    def test_body_order_is_preserved(self) -> None:
        body = (
            HeadingBlock(level=1, text="A"),
            HeadingBlock(level=2, text="B"),
            HeadingBlock(level=3, text="C"),
        )
        doc = _make_doc(body=body)
        heading_texts = tuple(b.text for b in doc.body if isinstance(b, HeadingBlock))
        assert heading_texts == ("A", "B", "C")


class TestBlockDiscriminator:
    """The ``block_kind`` field selects the right concrete block."""

    def test_each_block_kind_round_trips(self) -> None:
        blocks: tuple[object, ...] = (
            HeadingBlock(level=2, text="Heading"),
            ProseBlock(text="paragraph"),
            BulletListBlock(items=("alpha", "beta")),
            CodeBlock(code="print(1)", language="python"),
            DecisionBlock(decision="picked X", rationale="cheaper"),
            MetricBlock(name="conv", value="0.12", unit="ratio"),
            LinkBlock(label="design", url="https://example.com/d"),
        )
        doc = _make_doc(body=blocks)
        dumped = doc.model_dump(mode="json")
        rehydrated = LivingDocument.model_validate(dumped)
        assert tuple(type(b) for b in rehydrated.body) == tuple(type(b) for b in blocks)

    def test_unknown_block_kind_rejected(self) -> None:
        body_dump = [{"block_kind": "wat", "block_id": "id1", "text": "x"}]
        payload = {
            "slug": "s",
            "title": "t",
            "doc_type": "status_report",
            "author_agent_id": "a",
            "body": body_dump,
            "created_at": _ts().isoformat(),
            "updated_at": _ts().isoformat(),
        }
        with pytest.raises(ValidationError):
            LivingDocument.model_validate(payload)


class TestBlockBounds:
    """Block payloads reject overrun and empty-required inputs."""

    def test_heading_level_clamped(self) -> None:
        with pytest.raises(ValidationError):
            HeadingBlock(level=0, text="too low")
        with pytest.raises(ValidationError):
            HeadingBlock(level=7, text="too high")

    def test_bullet_list_requires_items(self) -> None:
        with pytest.raises(ValidationError):
            BulletListBlock(items=())

    def test_block_id_default_is_unique(self) -> None:
        a = HeadingBlock(level=2, text="x")
        b = HeadingBlock(level=2, text="x")
        assert a.block_id != b.block_id
