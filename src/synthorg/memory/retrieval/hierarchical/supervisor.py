"""Supervisor router -- LLM-based query routing and retry evaluation.

Uses a small-tier model to decide which retrieval workers to invoke
and whether to retry with corrected queries when results are poor.
"""

import builtins
import json
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.budget.call_category import LLMCallCategory

# ``CostTrackerProtocol``, ``CompletionProvider``, ``RetrievalQuery`` and
# ``FinalRetrievalResult`` are part of ``SupervisorRouter``'s public
# annotation surface (constructor + ``route`` + ``evaluate_for_retry``)
# so they must resolve at runtime when downstream tooling evaluates
# type hints (DI containers, doc generators).
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.boundary import parse_typed
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
from synthorg.memory.retrieval.models import (
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
from synthorg.providers.protocol import CompletionProvider

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

# Both calls emit a short JSON object: routing returns a worker list +
# reason; retry returns a verdict, an optional corrected query, an
# optional strategy, and a free-text reason. 256 tokens covers the
# widest of these with headroom while bounding cost regardless of the
# provider default.
_ROUTING_MAX_TOKENS: Final[int] = 256

# Routing decisions and retry-evaluation must be deterministic so the
# same query produces the same worker selection across runs; pin
# ``temperature=0.0`` regardless of provider defaults.
_ROUTING_COMPLETION_CONFIG: Final[CompletionConfig] = CompletionConfig(
    temperature=0.0,
    max_tokens=_ROUTING_MAX_TOKENS,
)


class _LlmRoutingResponse(
    BaseModel
):  # lint-allow: frozen-extra-forbid -- LLM JSON may carry extra keys; only routing fields are read  # noqa: E501
    """Validated shape of the routing model's JSON response.

    Lenient by design: an absent ``workers`` defaults to empty (the
    caller filters against the worker allowlist and falls back when
    nothing valid remains), and ``extra="ignore"`` tolerates any
    commentary keys the model emits.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")

    workers: list[str] = Field(default_factory=list)
    reason: str = "LLM routing decision"


class _LlmRetryResponse(
    BaseModel
):  # lint-allow: frozen-extra-forbid -- LLM JSON may carry extra keys; only retry fields are read  # noqa: E501
    """Validated shape of the retry-evaluation model's JSON response.

    Lenient by design (defaults + ``extra="ignore"``): a malformed
    response degrades to ``retry=False`` rather than raising on the
    retry-evaluation path.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")

    retry: bool = False
    corrected_query: str | None = None
    alternative_strategy: Literal["semantic_only", "episodic_only", "skip"] | None = (
        None
    )
    reason: str = "LLM suggested retry"

    @field_validator("alternative_strategy", mode="before")
    @classmethod
    def _nullify_unknown_strategy(cls, value: object) -> object:
        """Coerce an unrecognised strategy to ``None`` rather than rejecting.

        Keeps the retry response usable when the model proposes a
        strategy outside the supported set: the correction proceeds with
        its ``corrected_query`` and no alternative strategy.

        Returns:
            The value when it is a supported strategy, else ``None``.
        """
        if isinstance(value, str) and value in {
            "semantic_only",
            "episodic_only",
            "skip",
        }:
            return value
        return None


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
        cost_tracker: CostTrackerProtocol | None = None,
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
        routing = parse_typed("memory.routing", parsed, _LlmRoutingResponse)
        workers = tuple(w for w in routing.workers if w in _VALID_WORKERS)[
            : self._max_workers
        ]
        if not workers:
            logger.debug(
                MEMORY_HIERARCHICAL_ROUTING,
                action="invalid_workers_filtered",
                llm_workers=list(routing.workers),
            )
            workers = _DEFAULT_FALLBACK_WORKERS
        reason = routing.reason
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
        # Wrap the untrusted ``query.text`` so a malicious query body cannot
        # inject instructions into the retry evaluator. The candidate-count
        # summary is fixed-format numeric data, so it stays outside the fence.
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
            logger.warning(
                MEMORY_HIERARCHICAL_RETRY,
                action="eval_failed",
                reason="empty_content",
            )
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
        retry = parse_typed("memory.retry", parsed, _LlmRetryResponse)
        if not retry.retry:
            logger.debug(
                MEMORY_HIERARCHICAL_RETRY,
                action="llm_no_retry",
                reason=retry.reason,
            )
            return None

        corrected_query = None
        corrected_text = retry.corrected_query
        if corrected_text:
            try:
                corrected_query = type(query).model_validate(
                    query.model_dump(mode="python") | {"text": corrected_text},
                )
            except builtins.MemoryError, RecursionError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.debug(
                    MEMORY_HIERARCHICAL_RETRY,
                    action="corrected_query_invalid",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    corrected_length=len(str(corrected_text)),
                )
                corrected_query = None

        logger.info(
            MEMORY_HIERARCHICAL_RETRY,
            action="correction",
            has_corrected_query=corrected_query is not None,
            alternative_strategy=retry.alternative_strategy,
            reason=retry.reason,
        )
        return RetrievalRetryCorrection(
            corrected_query=corrected_query,
            alternative_strategy=retry.alternative_strategy,
            reason=retry.reason,
        )
