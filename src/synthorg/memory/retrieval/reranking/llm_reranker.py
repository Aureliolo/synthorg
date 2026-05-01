"""LLM-based query-specific re-ranker.

Scores retrieval candidates against the current query using a
small-tier model and re-orders by LLM-assigned ranking.
"""

import builtins
import hashlib
import json

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_RERANK_CACHE_MISS,
    MEMORY_RERANK_COMPLETE,
    MEMORY_RERANK_FAILED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

# Re-ranking must be deterministic across CI shards so cache keys
# remain stable. Temperature=0.0 also minimises the chance of the
# LLM returning a malformed ranking array that forces a fallback.
_RERANK_COMPLETION_CONFIG = CompletionConfig(temperature=0.0)

# ``CostTracker``, ``CompletionProvider``, ``RerankerCache``,
# ``RetrievalQuery`` and ``RetrievalCandidate`` are part of
# ``LLMQuerySpecificReranker.__init__`` and ``rerank``'s public
# annotation, so they must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker  # noqa: TC001, E402
from synthorg.memory.retrieval.models import (  # noqa: TC001, E402
    RetrievalCandidate,
    RetrievalQuery,
)
from synthorg.memory.retrieval.reranking.cache import RerankerCache  # noqa: TC001, E402
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001, E402

logger = get_logger(__name__)

_RERANK_SYSTEM_PROMPT = (
    """\
You are a retrieval re-ranker. Given a query and a list of memory \
candidates, rank them by relevance to the query.

Respond with JSON: {{"ranking": [idx0, idx1, ...]}} where each idx \
is the 0-based index of a candidate in the input list, ordered from \
most to least relevant. Include ALL indices exactly once.
"""
    "\n\n" + untrusted_content_directive((TAG_TASK_DATA,))
)

_MAX_CANDIDATE_CONTENT_CHARS = 500


def _build_cache_key(
    query_text: str,
    candidate_ids: tuple[str, ...],
) -> str:
    """Build a deterministic cache key from query + candidate IDs."""
    raw = query_text + "|" + ",".join(sorted(candidate_ids))
    return hashlib.sha256(raw.encode()).hexdigest()


class LLMQuerySpecificReranker:
    """LLM-based query-specific re-ranker.

    Calls a small-tier model to re-rank candidates by query relevance.
    The ordering reflects the LLM's ranking signal; original
    ``combined_score`` values are preserved so downstream filtering
    keeps its semantics.  Falls back to original order on any LLM
    failure (``builtins.MemoryError`` / ``RecursionError`` still
    propagate).

    Args:
        provider: Completion provider for LLM calls.
        model: Model identifier (small-tier recommended).
        cache: Optional re-ranker cache for amortising cost.
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        cache: RerankerCache | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._cache = cache
        self._cost_tracker = cost_tracker

    async def rerank(  # noqa: C901
        self,
        query: RetrievalQuery,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RetrievalCandidate, ...]:
        """Re-rank candidates using query-specific LLM scoring.

        Falls back to the original order on LLM failure (system-level
        errors like ``MemoryError`` and ``RecursionError`` still
        propagate).  Original ``combined_score`` values are preserved
        on reranked candidates -- the LLM signal is encoded only in
        the output sequence order.

        Args:
            query: The original retrieval query.
            candidates: Post-fusion candidates to re-rank.

        Returns:
            Candidates in re-ordered sequence with original scores
            preserved.
        """
        if len(candidates) <= 1:
            return candidates

        candidate_ids = tuple(c.entry.id for c in candidates)
        # Duplicate IDs would silently collapse in the ``by_id`` map
        # below and corrupt cached-ranking replay.  Skip both cache
        # read/write in that edge case.
        cache_eligible = self._cache is not None and len(set(candidate_ids)) == len(
            candidate_ids
        )
        by_id = {c.entry.id: c for c in candidates}
        cache_key = ""

        # Check cache -- returns stored ID ordering, we reapply to
        # the current candidate set so fresh state always wins.
        # Cache faults degrade to a cold rerank rather than failing
        # retrieval; this is an optional optimization.
        if cache_eligible and self._cache is not None:
            cache_key = _build_cache_key(query.text, candidate_ids)
            try:
                cached_ids = await self._cache.get(cache_key)
            except builtins.MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    MEMORY_RERANK_CACHE_MISS,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    reason="cache_get_failed",
                )
                cached_ids = None
            if cached_ids is not None and set(cached_ids) == set(by_id):
                return tuple(by_id[cid] for cid in cached_ids)

        try:
            reranked = await self._rerank_via_llm(query, candidates)
        except builtins.MemoryError, RecursionError:
            # Emit failure telemetry before re-raising so operators
            # can correlate system-level reranker aborts with the
            # triggering query/candidate set.
            logger.exception(
                MEMORY_RERANK_FAILED,
                error="system_error",
                candidate_count=len(candidates),
                query_length=len(query.text),
            )
            raise
        except Exception as exc:
            # Reranking is optional post-fusion enhancement -- degrade
            # to the pre-rerank order on any provider failure
            # (including ``RetryExhaustedError``) rather than aborting
            # retrieval.  The parent pipeline's ranked results are
            # still usable.
            logger.warning(
                MEMORY_RERANK_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                candidate_count=len(candidates),
                query_length=len(query.text),
            )
            return candidates
        else:
            if cache_eligible and self._cache is not None and cache_key:
                try:
                    await self._cache.put(
                        cache_key,
                        tuple(c.entry.id for c in reranked),
                    )
                except builtins.MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.warning(
                        MEMORY_RERANK_CACHE_MISS,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        reason="cache_put_failed",
                    )
            logger.info(
                MEMORY_RERANK_COMPLETE,
                candidate_count=len(reranked),
                query_length=len(query.text),
            )
            return reranked

    async def _rerank_via_llm(
        self,
        query: RetrievalQuery,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RetrievalCandidate, ...]:
        """Call LLM for re-ranking decision."""
        candidate_lines = []
        for i, c in enumerate(candidates):
            content_preview = c.entry.content[:_MAX_CANDIDATE_CONTENT_CHARS]
            candidate_lines.append(
                f"[{i}] score={c.combined_score:.2f} content={content_preview!r}",
            )
        # Both the query text and the candidate payload come from
        # attacker-controllable storage (stored memories, user
        # queries) and must be fenced with ``wrap_untrusted`` so the
        # model cannot confuse data for instructions. The system
        # prompt appends ``untrusted_content_directive`` at import
        # time so the model is explicitly told what lives inside
        # ``<task-data>`` tags.
        wrapped_query = wrap_untrusted(TAG_TASK_DATA, query.text)
        wrapped_candidates = wrap_untrusted(
            TAG_TASK_DATA,
            "\n".join(candidate_lines),
        )
        user_content = f"Query:\n{wrapped_query}\n\nCandidates:\n{wrapped_candidates}"
        messages: list[ChatMessage] = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=_RERANK_SYSTEM_PROMPT,
            ),
            ChatMessage(role=MessageRole.USER, content=user_content),
        ]
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=query.agent_id,
            task_id=NotBlankStr("system:memory:rerank"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await self._provider.complete(
                messages,
                self._model,
                config=_RERANK_COMPLETION_CONFIG,
            )
        if response.content is None:
            logger.debug(
                MEMORY_RERANK_FAILED,
                error="LLM returned null content",
                candidate_count=len(candidates),
            )
            return candidates

        parsed = json.loads(response.content)
        ranking: list[int] = parsed.get("ranking", [])

        # Validate ranking indices
        n = len(candidates)
        if sorted(ranking) != list(range(n)):
            logger.debug(
                MEMORY_RERANK_FAILED,
                error="Invalid ranking indices from LLM",
                expected_count=n,
                received=ranking,
            )
            return candidates

        # Re-order preserving original combined_score so downstream
        # filtering/diversity logic still sees the true relevance signal.
        # The re-ranking intent is communicated via sequence order.
        return tuple(candidates[idx] for idx in ranking)
