# mypy: disable-error-code="explicit-any"
"""Integration tests for ``ContextInjectionStrategy`` diversity re-ranking.

Verifies that the retrieval pipeline actually wires
``apply_diversity_penalty`` into ``_execute_pipeline`` when
``diversity_penalty_enabled=True``.  Uses ``monkeypatch`` to stub the
diversity function directly so the test does not depend on the
tie-breaking details of the real MMR algorithm.  Kept in a dedicated
file (split from ``test_retriever.py``) to avoid growing the
already-over-800-line main file.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy


def _make_entry(
    *,
    entry_id: str,
    content: str,
    relevance_score: float = 0.8,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id="agent-1",
        category=MemoryCategory.EPISODIC,
        content=content,
        metadata=MemoryMetadata(),
        created_at=datetime.now(UTC),
        relevance_score=relevance_score,
    )


def _make_backend(entries: tuple[MemoryEntry, ...]) -> AsyncMock:
    backend = AsyncMock()
    backend.retrieve = AsyncMock(return_value=entries)
    backend.supports_sparse_search = False
    return backend


@pytest.mark.unit
class TestDiversityPenaltyPipelineIntegration:
    """End-to-end wiring of ``diversity_penalty_enabled`` in the pipeline."""

    async def test_diversity_penalty_enabled_invokes_mmr_and_respects_ordering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MMR is invoked and its output ordering is respected.

        Installs a stub ``apply_diversity_penalty`` that (a) records
        its call arguments and (b) reverses the input order, then
        asserts both.  Stubbing avoids dependency on the tie-breaking
        details of the real MMR algorithm while still proving the
        pipeline calls the right function with the right parameters
        and uses its output.
        """
        e_alpha = _make_entry(
            entry_id="alpha",
            content="first alpha content",
            relevance_score=0.9,
        )
        e_beta = _make_entry(
            entry_id="beta",
            content="second beta content",
            relevance_score=0.8,
        )
        e_gamma = _make_entry(
            entry_id="gamma",
            content="third gamma content",
            relevance_score=0.7,
        )
        entries = (e_alpha, e_beta, e_gamma)

        calls: list[tuple[int, float]] = []

        def _stub_diversity_penalty(
            scored: Any,
            *,
            diversity_lambda: float,
            similarity_fn: Any = None,
        ) -> Any:
            del similarity_fn  # intentionally unused in the stub
            calls.append((len(scored), diversity_lambda))
            return tuple(reversed(scored))

        monkeypatch.setattr(
            "synthorg.memory.retriever.apply_diversity_penalty",
            _stub_diversity_penalty,
        )

        strategy = ContextInjectionStrategy(
            backend=_make_backend(entries),
            config=MemoryRetrievalConfig(
                diversity_penalty_enabled=True,
                diversity_lambda=0.4,
                min_relevance=0.0,
                max_memories=20,
            ),
        )
        messages = await strategy.prepare_messages(
            "agent-1", "query", token_budget=2000
        )

        assert calls, "enabled pipeline did not call apply_diversity_penalty"
        assert calls[0] == (3, 0.4)

        content = "\n".join((m.content or "") for m in messages)
        alpha_pos = content.find("first alpha")
        beta_pos = content.find("second beta")
        gamma_pos = content.find("third gamma")
        assert alpha_pos >= 0
        assert beta_pos >= 0
        assert gamma_pos >= 0
        # Stub reversed the order so gamma now comes before alpha.
        assert gamma_pos < beta_pos < alpha_pos, (
            "Pipeline must respect the MMR output ordering, not the "
            f"original relevance order.  Got:\n{content}"
        )

    async def test_diversity_penalty_disabled_does_not_invoke_mmr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pipeline must not call ``apply_diversity_penalty`` when the flag is off.

        Installs a stub that raises if invoked, so any accidental call
        during the disabled path fails the test loudly (rather than
        silently returning a re-ordered tuple that happens to match
        the disabled-path expectation).
        """
        entries = (
            _make_entry(entry_id="a", content="one two three", relevance_score=0.9),
            _make_entry(entry_id="b", content="four five six", relevance_score=0.8),
        )

        _disabled_mmr_msg = (
            "apply_diversity_penalty must not be called when "
            "diversity_penalty_enabled=False"
        )

        def _boom(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs  # intentionally unused
            raise AssertionError(_disabled_mmr_msg)

        monkeypatch.setattr(
            "synthorg.memory.retriever.apply_diversity_penalty",
            _boom,
        )

        strategy = ContextInjectionStrategy(
            backend=_make_backend(entries),
            config=MemoryRetrievalConfig(
                diversity_penalty_enabled=False,
                min_relevance=0.0,
            ),
        )
        messages = await strategy.prepare_messages(
            "agent-1", "query", token_budget=2000
        )
        content = "\n".join((m.content or "") for m in messages)
        pos_a = content.find("one two three")
        pos_b = content.find("four five six")
        assert pos_a >= 0
        assert pos_b >= 0
        assert pos_a < pos_b
