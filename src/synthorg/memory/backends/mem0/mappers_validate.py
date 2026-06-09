"""Validation of raw Mem0 SDK responses before domain mapping.

Stateless guards that assert the shape of ``Memory.add()`` and
retrieval responses, raising domain errors on malformed payloads so
the mappers downstream only ever see well-formed dicts.
"""

from synthorg.core.types import NotBlankStr
from synthorg.memory.errors import (
    MemoryRetrievalError,
    MemoryStoreError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_ENTRY_RETRIEVAL_FAILED,
    MEMORY_ENTRY_STORE_FAILED,
)

logger = get_logger(__name__)


def validate_add_result(result: object, *, context: str) -> NotBlankStr:
    """Extract and validate the memory ID from a Mem0 ``add`` result.

    Args:
        result: Raw result from ``Memory.add()`` (expected dict).
        context: Human-readable context for error messages
            (e.g. ``"store"`` or ``"shared publish"``).

    Returns:
        The backend-assigned memory ID.

    Raises:
        MemoryStoreError: If the result is missing or malformed.
    """
    if not isinstance(result, dict):
        msg = (
            f"Mem0 add returned unexpected type for {context}: {type(result).__name__}"
        )
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    results_list = result.get("results")
    if not isinstance(results_list, list) or not results_list:
        msg = f"Mem0 add returned no results for {context}"
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    first = results_list[0]
    if not isinstance(first, dict):
        msg = (
            f"Mem0 add result item is not a dict for {context}: {type(first).__name__}"
        )
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    raw_id = first.get("id")
    if raw_id is None or not str(raw_id).strip():
        msg = (
            f"Mem0 add result has missing or blank 'id' for {context}: "
            f"keys={list(first.keys())}"
        )
        logger.warning(MEMORY_ENTRY_STORE_FAILED, context=context, error=msg)
        raise MemoryStoreError(msg)
    return NotBlankStr(str(raw_id))


def validate_mem0_result(
    raw_result: object,
    *,
    context: str,
) -> list[dict[str, object]]:
    """Validate and extract the results list from a Mem0 response.

    Args:
        raw_result: Raw return value from a Mem0 SDK call.
        context: Human-readable context for error messages.

    Returns:
        The ``"results"`` list from the response.

    Raises:
        MemoryRetrievalError: If the response is not a dict or
            ``"results"`` is not a list.
    """
    if not isinstance(raw_result, dict):
        msg = (
            f"Unexpected Mem0 response type for {context}: "
            f"{type(raw_result).__name__}, expected dict"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    if "results" not in raw_result:
        msg = (
            f"Mem0 response missing 'results' key for {context}: "
            f"keys={list(raw_result.keys())}"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    raw_list = raw_result["results"]
    if not isinstance(raw_list, list):
        msg = (
            f"Unexpected Mem0 results type for {context}: "
            f"{type(raw_list).__name__}, expected list"
        )
        logger.warning(
            MEMORY_ENTRY_RETRIEVAL_FAILED,
            context=context,
            error=msg,
        )
        raise MemoryRetrievalError(msg)
    return raw_list
