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
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryStoreRequest
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


class _DenseCapableBackend(InMemoryBackend):
    """The real keyword backend, claiming semantic recall.

    ``supports_dense_search`` is a duck-typed capability rather than a
    protocol member, so a spec'd double cannot express it. Subclassing a
    real backend keeps the double protocol-complete while varying only
    the axis under test.
    """

    supports_dense_search = True


class TestScopeAppliesOnlyWithoutSemanticRecall:
    """Tag overlap is lexical, so it must not gate a dense backend.

    A lesson about rolling back a deployment shares no term with
    "revert the release". Gating on tags would drop exactly the match
    dense retrieval was bought to find, so the guard applies only where
    meaning-similarity is unavailable.
    """

    async def _recall_with(self, *, dense: bool) -> tuple[object, ...]:
        """Seed one off-tag lesson and recall it by a synonym query.

        Returns:
            The messages the strategy would inject.
        """
        from synthorg.memory.retrieval_config import MemoryRetrievalConfig
        from synthorg.memory.retriever import ContextInjectionStrategy

        backend = _DenseCapableBackend() if dense else InMemoryBackend()
        await backend.connect()
        await backend.store(
            NotBlankStr("agent-1"),
            MemoryStoreRequest(
                category=MemoryCategory.PROCEDURAL,
                content=NotBlankStr("Roll back the release before draining."),
                metadata=MemoryMetadata(tags=(NotBlankStr("rollback"),)),
            ),
        )
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(min_relevance=0.0),
        )
        return await strategy.prepare_messages(
            recall_request(query="revert the release", token_budget=2000)
        )

    async def test_dense_backend_keeps_an_off_tag_semantic_match(self) -> None:
        assert await self._recall_with(dense=True)

    async def test_keyword_backend_still_drops_an_off_tag_lesson(self) -> None:
        assert await self._recall_with(dense=False) == ()
