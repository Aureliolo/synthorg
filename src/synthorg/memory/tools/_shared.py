"""Shared helpers for memory tool classes."""

from synthorg.memory.tool_retriever import ERROR_PREFIX


def _is_error_response(text: str) -> bool:
    """Check whether the strategy response indicates an error.

    All user-facing error return values in ``tool_retriever`` are
    prefixed with :data:`ERROR_PREFIX` (the single source of truth), so
    a direct ``startswith`` check is both sufficient and cheaper than
    iterating a redundant tuple of specific prefixes.

    Returns:
        ``True`` when the predicate holds, ``False`` otherwise.
    """
    return text.startswith(ERROR_PREFIX)
