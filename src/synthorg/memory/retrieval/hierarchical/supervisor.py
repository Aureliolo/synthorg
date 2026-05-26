"""Supervisor router -- LLM-based query routing and retry evaluation.

Uses a small-tier model to decide which retrieval workers to invoke
and whether to retry with corrected queries when results are poor.
"""

import builtins
import json
from typing import Final

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker``, ``CompletionProvider``, ``RetrievalQuery`` and
# ``FinalRetrievalResult`` are part of ``SupervisorRouter``'s public
# annotation surface (constructor + ``route`` + ``evaluate_for_retry``)
# so they must resolve at runtime when downstream tooling evaluates
# type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.memory.retrieval.hierarchical.models import (
    RetrievalRetryCorrection,
    WorkerRoutingDecision,
)
from synthorg.memory.retrieval.models import (  # noqa: TC001
    FinalRetrievalResult,
    RetrievalQuery,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_HIERARCHICAL_RETRY,
    MEMORY_HIERARCHICAL_ROUTING,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

logger = get_logger(__name__)

_VALID_WORKERS = frozenset({"semantic", "episodic", "procedural"})

_UNTRUSTED_DIRECTIVE = untrusted_content_directive((TAG_TASK_DATA,))

_ROUTING_SYSTEM_PROMPT = (
    """\
You are a memory retrieval router. Given a query, decide which memory \
workers to invoke. Available workers:
- semantic: Full-spectrum hybrid search across all memory types
- episodic: Recent events and decisions (time-windowed)
- procedural: Skills, patterns, and how-to knowledge

Respond with JSON: {{"workers": ["worker1", ...], "reason": "..."}}
Select 1 to {max_workers} workers. Prefer "semantic" for broad queries. \
Use "episodic" for time-sensitive questions and "procedural" for how-to.

"""
    + _UNTRUSTED_DIRECTIVE
)

_RETRY_SYSTEM_PROMPT = (
    """\
You are evaluating retrieval quality. The original query returned \
{count} results with an average score of {avg_score:.2f}.

Decide if a retry is needed. If so, suggest ONE of:
- A corrected query (broader or more specific)
- An alternative strategy: "semantic_only", "episodic_only", or "skip"

Respond with JSON: {{"retry": true/false, "corrected_query": "..." \
or null, "alternative_strategy": "..." or null, "reason": "..."}}

"""
    + _UNTRUSTED_DIRECTIVE
)

_DEFAULT_QUALITY_THRESHOLD: Final[float] = 0.3
_DEFAULT_FALLBACK_WORKERS = ("semantic",)
_DEFAULT_MAX_WORKERS_PER_QUERY: Final[int] = 2
_DEFAULT_MAX_RETRY_COUNT: Final[int] = 2

# Routing decisions and retry-evaluation must be deterministic so the
# same query produces the same worker selection across runs; pin
# ``temperature=0.0`` regardless of provider defaults.
_ROUTING_COMPLETION_CONFIG = CompletionConfig(temperature=0.0)


class SupervisorRouter:
    """LLM-based routing supervisor for hierarchical retrieval.

    Args:
        provider: Completion provider for LLM calls.
        model: Model identifier (typically small-tier).
        max_workers_per_query: Maximum workers per query.
        reflective_retry_enabled: Whether retry evaluation is active.
        max_retry_count: Maximum retry attempts.
        quality_threshold: Average score below which retry is considered
            (derived from ``MemoryRetrievalConfig.min_relevance``).
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        max_workers_per_query: int = _DEFAULT_MAX_WORKERS_PER_QUERY,
        reflective_retry_enabled: bool = True,
        max_retry_count: int = _DEFAULT_MAX_RETRY_COUNT,
        quality_threshold: float = _DEFAULT_QUALITY_THRESHOLD,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_workers = max_workers_per_query
        self._retry_enabled = reflective_retry_enabled
        self._max_retries = max_retry_count
        self._quality_threshold = quality_threshold
        self._cost_tracker = cost_tracker

    @property
    def reflective_retry_enabled(self) -> bool:
        """Whether reflective retry is active."""
        return self._retry_enabled

    @property
    def max_retry_count(self) -> int:
        """Maximum retry attempts."""
        return self._max_retries

    async def route(
        self,
        query: RetrievalQuery,
    ) -> WorkerRoutingDecision:
        """Decide which workers to invoke for a query.

        Falls back to ``("semantic",)`` on any LLM failure.

        Args:
            query: The retrieval query to route.

        Returns:
            Routing decision with selected workers and reason.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            return await self._route_via_llm(query)
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            # Provider exceptions in str(exc) can carry the API
            # key; scrub before logging.
            logger.warning(
                MEMORY_HIERARCHICAL_ROUTING,
                action="fallback",
                reason="llm_routing_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                query_length=len(query.text),
            )
            return WorkerRoutingDecision(
                selected_workers=_DEFAULT_FALLBACK_WORKERS,
                reason="LLM routing fallback",
            )

    async def evaluate_for_retry(
        self,
        query: RetrievalQuery,
        result: FinalRetrievalResult,
    ) -> RetrievalRetryCorrection | None:
        """Evaluate result quality and suggest retry correction.

        Returns ``None`` when results are sufficient or retry is
        disabled.  Falls back to ``None`` on LLM failure.

        Args:
            query: The original retrieval query.
            result: The current retrieval result to evaluate.

        Returns:
            Retry correction if warranted, else ``None``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        if not self._retry_enabled:
            return None
        if not result.candidates:
            return RetrievalRetryCorrection(
                alternative_strategy="semantic_only",
                reason="No results returned, falling back to semantic",
            )
        avg_score = sum(c.combined_score for c in result.candidates) / len(
            result.candidates
        )
        if avg_score >= self._quality_threshold:
            return None
        try:
            return await self._evaluate_via_llm(query, result)
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEMORY_HIERARCHICAL_RETRY,
                action="eval_failed",
                reason="llm_retry_evaluation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _route_via_llm(
        self,
        query: RetrievalQuery,
    ) -> WorkerRoutingDecision:
        """Call LLM for routing decision.

        Returns:
            Result of type ``WorkerRoutingDecision``.

        Raises:
            ValueError: If an argument fails domain validation.
            JSONDecodeError: If the related operation fails.
        """
        system_prompt = _ROUTING_SYSTEM_PROMPT.format(
            max_workers=self._max_workers,
        )
        # ``query.text`` is operator-controlled but ultimately
        # sourced from upstream agent reasoning that may have
        # ingested untrusted content; wrap it in a ``<task-data>``
        # fence so the routing model treats it as data rather than
        # instruction.
        wrapped_query = wrap_untrusted(TAG_TASK_DATA, query.text)
        messages: list[ChatMessage] = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
            ChatMessage(role=MessageRole.USER, content=wrapped_query),
        ]
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=query.agent_id,
            task_id=NotBlankStr("system:memory:retrieval_route"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await self._provider.complete(
                messages,
                self._model,
                config=_ROUTING_COMPLETION_CONFIG,
            )
        if response.content is None:
            msg = "LLM returned empty content for routing"
            raise ValueError(msg)
        try:
            parsed = json.loads(response.content)
        except json.JSONDecodeError as exc:
            logger.warning(
                MEMORY_HIERARCHICAL_ROUTING,
                action="json_parse_failed",
                content_length=len(response.content),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        workers = tuple(
            w for w in parsed.get("workers", ["semantic"]) if w in _VALID_WORKERS
        )[: self._max_workers]
        if not workers:
            workers = _DEFAULT_FALLBACK_WORKERS
        reason = parsed.get("reason", "LLM routing decision")
        logger.info(
            MEMORY_HIERARCHICAL_ROUTING,
            action="decided",
            workers=list(workers),
            query_length=len(query.text),
        )
        return WorkerRoutingDecision(
            selected_workers=workers,
            reason=reason,
        )

    async def _evaluate_via_llm(
        self,
        query: RetrievalQuery,
        result: FinalRetrievalResult,
    ) -> RetrievalRetryCorrection | None:
        """Call LLM for retry evaluation.

        Returns:
            The resulting ``RetrievalRetryCorrection``, or ``None`` when unavailable.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        avg_score = sum(c.combined_score for c in result.candidates) / max(
            len(result.candidates), 1
        )
        system_prompt = _RETRY_SYSTEM_PROMPT.format(
            count=len(result.candidates),
            avg_score=avg_score,
        )
        # Wrap the untrusted ``query.text`` so a malicious query
        # body cannot inject instructions into the retry evaluator.
        # The candidate-count summary is fixed-format numeric data,
        # so it stays outside the fence.
        wrapped_query = wrap_untrusted(TAG_TASK_DATA, query.text)
        user_content = (
            f"Original query:\n{wrapped_query}\n"
            f"Results: {len(result.candidates)} candidates, "
            f"avg score: {avg_score:.2f}"
        )
        messages: list[ChatMessage] = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
            ChatMessage(role=MessageRole.USER, content=user_content),
        ]
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=query.agent_id,
            task_id=NotBlankStr("system:memory:retrieval_retry"),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await self._provider.complete(
                messages,
                self._model,
                config=_ROUTING_COMPLETION_CONFIG,
            )
        if response.content is None:
            return None
        try:
            parsed = json.loads(response.content)
        except json.JSONDecodeError as exc:
            logger.warning(
                MEMORY_HIERARCHICAL_RETRY,
                action="json_parse_failed",
                content_length=len(response.content),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        if not parsed.get("retry", False):
            return None

        corrected_query = None
        corrected_text = parsed.get("corrected_query")
        if corrected_text:
            try:
                corrected_query = type(query).model_validate(
                    query.model_dump(mode="python") | {"text": corrected_text},
                )
            except builtins.MemoryError, RecursionError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                logger.debug(
                    MEMORY_HIERARCHICAL_RETRY,
                    action="corrected_query_invalid",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    corrected_length=len(str(corrected_text)),
                )
                corrected_query = None

        alt_strategy = parsed.get("alternative_strategy")
        if alt_strategy and alt_strategy not in {
            "semantic_only",
            "episodic_only",
            "skip",
        }:
            alt_strategy = None

        reason = parsed.get("reason", "LLM suggested retry")
        logger.info(
            MEMORY_HIERARCHICAL_RETRY,
            action="correction",
            has_corrected_query=corrected_query is not None,
            alternative_strategy=alt_strategy,
            reason=reason,
        )
        return RetrievalRetryCorrection(
            corrected_query=corrected_query,
            alternative_strategy=alt_strategy,
            reason=reason,
        )
