"""Propagation helper for interpreter-critical exceptions.

`MemoryError` and `RecursionError` are subclasses of `Exception`, so a
broad ``except Exception:`` handler silently swallows them along with the
ordinary business-logic exceptions it was written to catch. The codebase
must never absorb either of these: an out-of-memory or stack-overflow
condition is fatal at the interpreter level and must propagate to the
top of the stack so the worker / API process surfaces the failure
(crash + restart) rather than logging a warning and continuing in a
corrupted state.

The canonical pattern is to install an explicit re-raise before the
broad handler::

    try:
        ...
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        logger.warning(EVENT, error_type=type(exc).__name__, ...)

This module replaces the leading ``except (MemoryError, RecursionError):
raise`` clause with a single call to :func:`reraise_critical` inside the
broad handler::

    try:
        ...
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(EVENT, error_type=type(exc).__name__, ...)

The behaviour is identical (``MemoryError`` / ``RecursionError`` still
propagate before any logging or business logic runs) but the explicit
narrow ``except`` clause is gone, so ruff `DOC501` no longer demands
that every async helper docstring document `MemoryError` and
`RecursionError` in its `Raises:` section.

`asyncio.CancelledError` is **not** routed through this helper because
it is a subclass of `BaseException`, not `Exception` -- broad
``except Exception:`` blocks never catch it in the first place, so no
re-raise is necessary.
"""

from typing import Final

_CRITICAL_TYPES: Final[tuple[type[BaseException], ...]] = (
    MemoryError,
    RecursionError,
)


def reraise_critical(exc: BaseException) -> None:
    """Re-raise ``exc`` if it is an interpreter-critical exception.

    Intended for use as the first statement of a broad
    ``except Exception as exc:`` block, immediately before any logging
    or recovery logic. Returns silently when ``exc`` is not critical so
    the caller can continue with its normal error-handling flow.

    Args:
        exc: The caught exception. May be any ``BaseException`` instance;
            the helper is permissive about the input type so that a
            future broadening of the surrounding ``except`` clause does
            not silently bypass the re-raise.

    Returns:
        ``None`` when ``exc`` is not ``MemoryError`` or ``RecursionError``.
        The caller is expected to continue with its own logging /
        cleanup / re-raise logic.

    Raises:
        MemoryError: Re-raised unchanged when ``exc`` is a ``MemoryError``.
        RecursionError: Re-raised unchanged when ``exc`` is a
            ``RecursionError``.
    """
    if isinstance(exc, _CRITICAL_TYPES):
        raise exc
