# module-kind: service
"""Query-generation strategies for fine-tune training-data synthesis.

Stage 1 of the fine-tune pipeline turns each document chunk into a
synthetic ``(query, positive_passage)`` pair. Two pluggable strategies
satisfy the :class:`QueryGenerator` protocol:

* :class:`ExtractiveQueryGenerator` -- the dependency-free default that
  derives a query from the chunk's leading sentence.
* :class:`LlmQueryGenerator` -- calls a :class:`CompletionProvider` to
  synthesise a natural retrieval query, falling back to the extractive
  form for a transient failure or an empty completion so a single bad
  chunk never aborts the run. A non-retryable provider error (bad model
  id, auth) propagates so a misconfiguration fails the run fast instead
  of silently degrading the whole corpus.

:func:`build_query_generator` selects the LLM strategy only when both a
provider and a non-blank model are supplied; otherwise it returns the
extractive default.
"""

from typing import Final, Protocol, runtime_checkable

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_UNTRUSTED_ARTIFACT,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_QUERY_LLM_FALLBACK,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_EXTRACTIVE_SNIPPET_CHARS: Final[int] = 100
_QUERY_MAX_CHARS: Final[int] = 200
_DEFAULT_MAX_QUERY_TOKENS: Final[int] = 64
_DEFAULT_TEMPERATURE: Final[float] = 0.3

_SYSTEM_PROMPT = (
    "You generate retrieval-training data. Given a document passage,"
    " write ONE concise natural-language search query that the passage"
    " directly answers. Return only the query text, with no preamble,"
    " quotation marks, or trailing punctuation.\n\n"
    + untrusted_content_directive((TAG_UNTRUSTED_ARTIFACT,))
)


def extractive_query(chunk: str) -> str:
    """Derive an extractive retrieval query from a chunk's lead sentence.

    Returns:
        A ``"Find information about: ..."`` query string.
    """
    sentences = chunk.split(".")
    first = sentences[0].strip() if sentences else chunk[:_EXTRACTIVE_SNIPPET_CHARS]
    if not first:
        first = chunk[:_EXTRACTIVE_SNIPPET_CHARS].strip()
    first = first[:_QUERY_MAX_CHARS]
    return f"Find information about: {first}"


def _normalise_llm_query(raw: str) -> str:
    """Reduce an LLM completion to a single bounded query line.

    Returns:
        The first non-empty line, stripped of wrapping quotes and
        bounded to ``_QUERY_MAX_CHARS``; ``""`` when nothing usable.
    """
    for line in raw.splitlines():
        candidate = line.strip().strip('"').strip("'").strip()
        if candidate:
            return candidate[:_QUERY_MAX_CHARS]
    return ""


@runtime_checkable
class QueryGenerator(Protocol):
    """Strategy that turns a document chunk into a retrieval query."""

    async def generate(self, chunk: str) -> str:
        """Return a retrieval query for ``chunk``.

        Args:
            chunk: A document chunk to derive a query from.

        Returns:
            A non-empty query string.
        """
        ...


class ExtractiveQueryGenerator:
    """Dependency-free query generator using the chunk's lead sentence."""

    async def generate(self, chunk: str) -> str:
        """Return the extractive query for ``chunk``.

        Returns:
            A ``"Find information about: ..."`` query string.
        """
        return extractive_query(chunk)


class LlmQueryGenerator:
    """LLM-backed query generator with an extractive fallback.

    Args:
        provider: Completion provider for the query-generation call.
        model: Model identifier (non-blank).
        max_query_tokens: Token ceiling for the generated query.
        temperature: Sampling temperature.
        cost_tracker: Optional tracker for cost attribution.

    Raises:
        ValueError: If ``model`` is blank.
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        max_query_tokens: int = _DEFAULT_MAX_QUERY_TOKENS,
        temperature: float = _DEFAULT_TEMPERATURE,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        if not model or not model.strip():
            msg = "model must be a non-blank string"
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._cost_tracker = cost_tracker
        self._config = CompletionConfig(
            temperature=temperature,
            max_tokens=max_query_tokens,
        )

    async def generate(self, chunk: str) -> str:
        """Generate a retrieval query for ``chunk`` via the LLM.

        Falls back to :func:`extractive_query` for a transient provider
        error or an empty completion. A non-retryable provider error
        propagates so a misconfigured model fails the run fast.

        Returns:
            The generated query, or the extractive fallback.

        Raises:
            ProviderError: On a non-retryable provider failure.
        """
        try:
            # The chunk is org-document content that may have absorbed
            # adversarial upstream text; fence it and pair with the
            # matching directive in the system prompt.
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                ChatMessage(
                    role=MessageRole.USER,
                    content=wrap_untrusted(TAG_UNTRUSTED_ARTIFACT, chunk),
                ),
            ]
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr("system:memory:fine_tune_query"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._model,
                    config=self._config,
                )
        except ProviderError as exc:
            if not exc.is_retryable:
                logger.warning(
                    MEMORY_FINE_TUNE_QUERY_LLM_FALLBACK,
                    model=self._model,
                    retryable=False,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            logger.warning(
                MEMORY_FINE_TUNE_QUERY_LLM_FALLBACK,
                model=self._model,
                retryable=True,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return extractive_query(chunk)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MEMORY_FINE_TUNE_QUERY_LLM_FALLBACK,
                model=self._model,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return extractive_query(chunk)
        query = _normalise_llm_query(response.content or "")
        return query or extractive_query(chunk)


def build_query_generator(
    *,
    provider: CompletionProvider | None,
    model: str | None,
    cost_tracker: CostTracker | None = None,
) -> QueryGenerator:
    """Select the query-generation strategy for a run.

    Returns:
        :class:`LlmQueryGenerator` when both a provider and a non-blank
        model are supplied; otherwise :class:`ExtractiveQueryGenerator`.
    """
    if provider is not None and model and model.strip():
        return LlmQueryGenerator(
            provider=provider,
            model=NotBlankStr(model.strip()),
            cost_tracker=cost_tracker,
        )
    return ExtractiveQueryGenerator()
