"""Agentic query reformulation for memory retrieval.

Provides protocols and LLM-based implementations for query rewriting
and sufficiency checking.  Used by ``ToolBasedInjectionStrategy`` to
iteratively improve retrieval quality.
"""

import builtins
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.models import MemoryEntry
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_REFORMULATION_FAILED,
    MEMORY_SUFFICIENCY_CHECK_FAILED,
)

logger = get_logger(__name__)

_REFORMULATE_PROMPT = (
    "You are a query reformulation assistant. Given an original search "
    "query and the current retrieval results (which may be insufficient), "
    "rewrite the query to improve recall. Focus on:\n"
    "- Adding synonyms and related terms\n"
    "- Expanding abbreviations\n"
    "- Including specific identifiers if implied\n"
    "- Broadening or narrowing scope as needed\n\n"
    "Original query: {query}\n"
    "Current results ({count} entries):\n"
    "<retrieved_memories>\n{results}\n</retrieved_memories>\n\n"
    "Respond with ONLY the rewritten query, nothing else."
)

_SUFFICIENCY_PROMPT = (
    "You are evaluating whether retrieval results sufficiently answer "
    "a query. Respond with exactly one word: SUFFICIENT or INSUFFICIENT.\n\n"
    "Query: {query}\n"
    "Results ({count} entries):\n"
    "<retrieved_memories>\n{results}\n</retrieved_memories>\n\n"
    "Are these results sufficient to answer the query?"
)


@runtime_checkable
class QueryReformulator(Protocol):
    """Protocol for query rewriting to improve retrieval."""

    async def reformulate(
        self,
        original_query: str,
        results: tuple[MemoryEntry, ...],
    ) -> str | None:
        """Rewrite a query for better retrieval.

        Args:
            original_query: The original search query.
            results: Current retrieval results.

        Returns:
            Rewritten query, or ``None`` if the original is sufficient
            or reformulation failed.
        """
        ...


@runtime_checkable
class SufficiencyChecker(Protocol):
    """Protocol for evaluating retrieval result quality."""

    async def check_sufficiency(
        self,
        query: str,
        results: tuple[MemoryEntry, ...],
    ) -> bool:
        """Check whether results adequately answer the query.

        Args:
            query: The search query.
            results: Retrieved memory entries.

        Returns:
            ``True`` if results are sufficient, ``False`` otherwise.
        """
        ...


def _sanitize_for_xml_block(text: str) -> str:
    """Escape content that could break XML-tagged prompt boundaries.

    Returns:
        Result of type ``str``.
    """
    return text.replace("</retrieved_memories>", "&lt;/retrieved_memories&gt;")


def _format_results_summary(entries: tuple[MemoryEntry, ...]) -> str:
    """Format up to 10 entries for LLM prompts, truncating at 200 chars.

    Returns:
        Result of type ``str``.
    """
    if not entries:
        return "(no results)"
    _max_len = 200
    parts: list[str] = []
    for e in entries[:10]:
        text = e.content[:_max_len]
        if len(e.content) > _max_len:
            text += "..."
        parts.append(f"- [{e.category.value}] {_sanitize_for_xml_block(text)}")
    return "\n".join(parts)


class LLMQueryReformulator:
    """LLM-based query reformulator.

    Uses a completion callback to rewrite queries for better
    retrieval recall.

    Args:
        completion_fn: Async callable that takes a prompt string
            and returns the LLM response text.
    """

    __slots__ = ("_completion_fn",)

    def __init__(
        self,
        *,
        completion_fn: Callable[[str], Awaitable[str]],
    ) -> None:
        self._completion_fn = completion_fn

    async def reformulate(
        self,
        original_query: str,
        results: tuple[MemoryEntry, ...],
    ) -> str | None:
        """Rewrite a query using LLM.

        Args:
            original_query: The original search query.
            results: Current retrieval results.

        Returns:
            Rewritten query, or ``None`` on failure or empty response.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            prompt = _REFORMULATE_PROMPT.format(
                query=original_query,
                count=len(results),
                results=_format_results_summary(results),
            )
            response = await self._completion_fn(prompt)
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEMORY_REFORMULATION_FAILED,
                original_query=original_query,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        response = response.strip()
        return response or None


class LLMSufficiencyChecker:
    """LLM-based sufficiency checker.

    Uses a completion callback to evaluate whether retrieval results
    adequately answer the query.

    Args:
        completion_fn: Async callable that takes a prompt string
            and returns the LLM response text.
    """

    __slots__ = ("_completion_fn",)

    def __init__(
        self,
        *,
        completion_fn: Callable[[str], Awaitable[str]],
    ) -> None:
        self._completion_fn = completion_fn

    async def check_sufficiency(
        self,
        query: str,
        results: tuple[MemoryEntry, ...],
    ) -> bool:
        """Evaluate result sufficiency using LLM.

        On error, defaults to ``True`` to prevent infinite
        reformulation loops.

        Args:
            query: The search query.
            results: Retrieved memory entries.

        Returns:
            ``True`` if sufficient or on error, ``False`` otherwise.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            prompt = _SUFFICIENCY_PROMPT.format(
                query=query,
                count=len(results),
                results=_format_results_summary(results),
            )
            response = await self._completion_fn(prompt)
            # Extract first token from last non-empty line, strip
            # punctuation, and check exact match.  This rejects
            # "NOT SUFFICIENT", "SUFFICIENT.", and "INSUFFICIENT".
            lines = [ln.strip() for ln in response.strip().splitlines() if ln.strip()]
            verdict = lines[-1].split()[0].strip(".,;:!?") if lines else ""
            return verdict.upper() == "SUFFICIENT"
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEMORY_SUFFICIENCY_CHECK_FAILED,
                query=query,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Default to sufficient to prevent infinite loops.
            return True
