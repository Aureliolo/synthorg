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
        reranker = LLMQuerySpecificReranker(
            provider=cast(CompletionProvider, provider),
            model="test-small-001",
            cache=None,
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
        await reranker._rerank_via_llm(query, candidates)
        provider.complete.assert_awaited_once()
        kwargs = provider.complete.await_args.kwargs
        assert kwargs.get("config") is _mod._RERANK_COMPLETION_CONFIG, (
            "provider.complete must receive the pinned "
            "_RERANK_COMPLETION_CONFIG so any refactor that constructs a "
            "fresh CompletionConfig fails this regression."
        )
