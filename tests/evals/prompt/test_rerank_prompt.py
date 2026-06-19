"""Prompt eval: memory re-ranker temperature contract."""

import inspect

import pytest


@pytest.mark.unit
class TestRerankPromptContract:
    """Guard rails for the LLM memory re-ranker prompt surface."""

    def test_system_prompt_defined(self) -> None:
        """Re-ranker must declare a pinned system prompt constant."""
        import re

        from synthorg.memory.retrieval.reranking import llm_reranker

        source = inspect.getsource(llm_reranker)
        assert re.search(r"(?m)^_RERANK_SYSTEM_PROMPT\s*=", source), (
            "llm_reranker must define the ``_RERANK_SYSTEM_PROMPT`` constant"
        )

    def test_temperature_is_zero(self) -> None:
        """Re-ranker must call the provider with temperature=0.0.

        Without this pin the reranker becomes non-deterministic across
        CI shards, which poisons the cache keys computed from
        ``(query_text, candidate_ids)`` -- cache entries pinned to one
        ranking would then return different orderings on re-computation.

        Checked both ways:

        1. **Definition**: a ``temperature=0.0`` binding appears in the
           module source (regex match on the CompletionConfig construction).
        2. **Usage**: the module exposes a ``_RERANK_COMPLETION_CONFIG``
           instance whose ``temperature`` attribute actually equals
           ``0.0`` at runtime. Source matching alone can be fooled by
           dead code or a commented example; runtime inspection proves
           the config object the reranker passes to
           ``self._provider.complete(..., config=...)`` is the pinned one.
        """
        import re

        from synthorg.memory.retrieval.reranking import llm_reranker

        source = inspect.getsource(llm_reranker)
        assert re.search(r"temperature\s*=\s*0(?:\.0+)?", source), (
            "llm_reranker must pin temperature=0.0 on its "
            "CompletionConfig for deterministic re-ranking"
        )
        runtime_config = llm_reranker._RERANK_COMPLETION_CONFIG
        assert runtime_config.temperature == 0.0, (
            "_RERANK_COMPLETION_CONFIG.temperature must equal 0.0 at runtime; "
            f"got {runtime_config.temperature!r}"
        )

    async def test_provider_complete_called_with_pinned_config(self) -> None:
        """Call-site proof: ``_rerank_via_llm`` passes the pinned config.

        The definition-level assertions above can pass while a future
        refactor slips a different config into the actual provider
        call. This exercises ``_rerank_via_llm`` with stub collaborators
        and asserts the ``config=`` kwarg the reranker hands to
        ``provider.complete(...)`` is exactly ``_RERANK_COMPLETION_CONFIG``.
        """
        import json
        from types import SimpleNamespace
        from typing import cast
        from unittest.mock import AsyncMock

        from typeguard import suppress_type_checks

        from synthorg.memory.retrieval.models import (
            RetrievalCandidate,
            RetrievalQuery,
        )
        from synthorg.memory.retrieval.reranking import llm_reranker as _mod
        from synthorg.memory.retrieval.reranking.llm_reranker import (
            LLMQuerySpecificReranker,
        )
        from synthorg.providers.protocol import CompletionProvider

        provider = SimpleNamespace(
            complete=AsyncMock(
                spec=CompletionProvider.complete,
                return_value=SimpleNamespace(content=json.dumps({"ranking": [0]})),
            ),
        )
        query = cast(
            RetrievalQuery,
            SimpleNamespace(text="needle", agent_id="test-agent"),
        )
        candidates = (
            cast(
                RetrievalCandidate,
                SimpleNamespace(
                    entry=SimpleNamespace(id="a", content="hay"),
                    combined_score=0.5,
                ),
            ),
        )
        # ``provider`` / ``query`` / ``candidates`` are structural stand-ins for
        # the ``CompletionProvider`` protocol and the retrieval models; this test
        # asserts the pinned ``config=`` reaches ``provider.complete``, not type
        # conformance of those doubles, so the runtime check is suppressed across
        # the reranker construction and the ``_rerank_via_llm`` call.
        with suppress_type_checks():
            reranker = LLMQuerySpecificReranker(
                provider=cast(CompletionProvider, provider),
                model="test-small-001",
                cache=None,
            )
            await reranker._rerank_via_llm(query, candidates)
        provider.complete.assert_awaited_once()
        kwargs = provider.complete.await_args.kwargs
        assert kwargs.get("config") is _mod._RERANK_COMPLETION_CONFIG, (
            "provider.complete must receive the pinned "
            "_RERANK_COMPLETION_CONFIG so any refactor that constructs a "
            "fresh CompletionConfig fails this regression."
        )


@pytest.mark.unit
class TestRerankRankingParse:
    """Labelled eval for the ranking-array parse contract.

    The prompt asks the LLM for ``{"ranking": [idx, ...]}``; this grades
    that a well-formed full permutation re-orders the candidates while
    every malformed array (wrong length, missing key, duplicate or
    out-of-range indices) fails closed to the original order rather than
    raising or dropping candidates.
    """

    async def _rerank_order(self, content: str) -> tuple[str, ...]:
        """Run ``_rerank_via_llm`` over two candidates ``a``/``b``.

        Returns the resulting id order so a permutation vs fallback is
        observable.
        """
        from types import SimpleNamespace
        from typing import cast
        from unittest.mock import AsyncMock

        from typeguard import suppress_type_checks

        from synthorg.memory.retrieval.models import (
            RetrievalCandidate,
            RetrievalQuery,
        )
        from synthorg.memory.retrieval.reranking.llm_reranker import (
            LLMQuerySpecificReranker,
        )
        from synthorg.providers.protocol import CompletionProvider

        provider = SimpleNamespace(
            complete=AsyncMock(
                spec=CompletionProvider.complete,
                return_value=SimpleNamespace(content=content),
            ),
        )
        query = cast(
            RetrievalQuery,
            SimpleNamespace(text="needle", agent_id="test-agent"),
        )
        candidates = tuple(
            cast(
                RetrievalCandidate,
                SimpleNamespace(
                    entry=SimpleNamespace(id=cid, content=cid),
                    combined_score=0.5,
                ),
            )
            for cid in ("a", "b")
        )
        with suppress_type_checks():
            reranker = LLMQuerySpecificReranker(
                provider=cast(CompletionProvider, provider),
                model="test-small-001",
                cache=None,
            )
            result = await reranker._rerank_via_llm(query, candidates)
        return tuple(c.entry.id for c in result)

    async def test_valid_permutation_reorders(self) -> None:
        """A full valid permutation re-orders the candidates."""
        import json

        assert await self._rerank_order(json.dumps({"ranking": [1, 0]})) == ("b", "a")

    @pytest.mark.parametrize(
        "ranking",
        [
            [0],  # wrong length
            [0, 0],  # duplicate indices
            [0, 2],  # out-of-range index
        ],
    )
    async def test_malformed_ranking_falls_back(self, ranking: list[int]) -> None:
        """A malformed ranking array preserves the original order."""
        import json

        assert await self._rerank_order(json.dumps({"ranking": ranking})) == ("a", "b")

    async def test_missing_ranking_key_falls_back(self) -> None:
        """A payload without a ``ranking`` key preserves the original order."""
        assert await self._rerank_order("{}") == ("a", "b")
