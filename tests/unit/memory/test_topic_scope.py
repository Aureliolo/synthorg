"""Tests for topic scoping of procedural recall.

Raw term-overlap scores cannot separate a genuinely relevant procedural
lesson from an incidentally-worded one: measured against the benchmark
suite, an unrelated brief shares more terms with a checkout lesson than
the checkout brief does. Scoping keys on the lesson's own tags instead,
so precision comes from structure rather than from a threshold.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.topic_scope import in_topic_scope, scope_terms
from tests._shared import recall_request

pytestmark = pytest.mark.unit


def _entry(
    *,
    tags: tuple[str, ...] = (),
    category: MemoryCategory = MemoryCategory.PROCEDURAL,
    content: str = "Apply the recorded corrected approach.",
) -> MemoryEntry:
    """Build a memory entry with the given tags and category."""
    return MemoryEntry(
        id=NotBlankStr("mem-1"),
        agent_id=NotBlankStr("agent-1"),
        category=category,
        content=NotBlankStr(content),
        metadata=MemoryMetadata(tags=tuple(NotBlankStr(t) for t in tags)),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
    )


class TestScopeTerms:
    def test_derives_terms_from_the_task_title(self) -> None:
        terms = scope_terms(recall_request(query="checkout resilience"))

        assert "checkout" in terms
        assert "resilience" in terms

    def test_drops_stop_words(self) -> None:
        """Stop words would match everything and scope nothing."""
        terms = scope_terms(recall_request(query="the checkout"))

        assert terms == frozenset({"checkout"})

    def test_ignores_the_wider_context(self) -> None:
        """Role and department are org-wide, so they cannot scope a topic."""
        terms = scope_terms(
            recall_request(
                query="checkout resilience",
                role="Developer",
                department="Engineering",
            )
        )

        assert "developer" not in terms
        assert "engineering" not in terms


class TestInTopicScope:
    def test_shared_tag_is_in_scope(self) -> None:
        terms = scope_terms(recall_request(query="checkout resilience"))

        assert in_topic_scope(_entry(tags=("checkout",)), terms)

    def test_unrelated_tag_is_out_of_scope(self) -> None:
        """The measured failure case: an unrelated brief, generic wording."""
        terms = scope_terms(recall_request(query="retrieval reranking"))

        assert not in_topic_scope(_entry(tags=("checkout",)), terms)

    def test_tag_matching_is_case_insensitive(self) -> None:
        terms = scope_terms(recall_request(query="Checkout resilience"))

        assert in_topic_scope(_entry(tags=("CHECKOUT",)), terms)

    def test_any_shared_tag_suffices(self) -> None:
        terms = scope_terms(recall_request(query="checkout resilience"))

        assert in_topic_scope(_entry(tags=("payments", "checkout")), terms)

    def test_untagged_procedural_entry_is_kept(self) -> None:
        """No tags means no structure to scope on; dropping it would
        silently disable lessons whose proposer emitted none."""
        terms = scope_terms(recall_request(query="retrieval reranking"))

        assert in_topic_scope(_entry(tags=()), terms)

    def test_non_procedural_categories_are_untouched(self) -> None:
        """Only procedural lessons are task-specific enough to scope."""
        terms = scope_terms(recall_request(query="retrieval reranking"))

        for category in (MemoryCategory.SEMANTIC, MemoryCategory.EPISODIC):
            assert in_topic_scope(_entry(tags=("checkout",), category=category), terms)

    def test_empty_scope_terms_constrain_nothing(self) -> None:
        """A title of nothing but stop words must not silence recall."""
        assert in_topic_scope(_entry(tags=("checkout",)), frozenset())
