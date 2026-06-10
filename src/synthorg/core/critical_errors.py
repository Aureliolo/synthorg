# module-kind: code
"""Propagation helper for interpreter-critical exceptions.

`MemoryError` and `RecursionError` are subclasses of `Exception`, so a
broad ``except Exception:`` handler silently swallows them along with the
ordinary business-logic exceptions it was written to catch. The codebase
must never absorb either of these: an out-of-memory or stack-overflow
condition is fatal at the interpreter level and must propagate to the
top of the stack so the worker / API process surfaces the failure
(crash + restart) rather than logging a warning and continuing in a
corrupted state.

Call :func:`reraise_critical` as the first statement of every broad
``except Exception as exc:`` block::

    try:
        ...
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(EVENT, error_type=type(exc).__name__, ...)

``MemoryError`` and ``RecursionError`` re-raise unchanged before any
logging or business logic runs; all other exceptions return silently
so the caller's recovery logic continues.

The call is deliberately placed BEFORE the ``logger.warning`` /
``logger.error`` in the handler, which is why the general "log
WARNING/ERROR before raising" guidance does NOT apply to this helper:
a fatal out-of-memory / stack-overflow condition must propagate
immediately, before the handler tries to allocate and emit a
structured log record (which can itself fail under memory pressure, or
add frames to an already-overflowing stack). Telemetry for the
ordinary (non-critical) failures is not lost: ``reraise_critical``
returns for them, so the WARNING/ERROR on the following line runs as
normal. The ordering is intentional; do not reorder these blocks to
log-before-reraise.

``asyncio.CancelledError`` is **not** routed through this helper because
it is a subclass of ``BaseException``, not ``Exception`` -- broad
``except Exception:`` blocks never catch it in the first place, so no
re-raise is necessary.

A critical exception raised inside an ``asyncio.TaskGroup`` child reaches
the surrounding handler wrapped in an ``ExceptionGroup``; the helper
unwraps groups recursively so a nested ``MemoryError`` / ``RecursionError``
still propagates before the handler logs, rather than being masked behind
the group's plain-``Exception`` identity.
"""

from typing import Final

_CRITICAL_TYPES: Final[tuple[type[BaseException], ...]] = (
    MemoryError,
    RecursionError,
)


def _contains_critical(exc: BaseException) -> bool:
    """Return whether ``exc`` is, or nests, an interpreter-critical exception."""
    if isinstance(exc, _CRITICAL_TYPES):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_contains_critical(child) for child in exc.exceptions)
    return False


def reraise_critical(exc: BaseException) -> None:
    """Re-raise ``exc`` if it is (or nests) an interpreter-critical exception.

    Intended for use as the first statement of a broad
    ``except Exception as exc:`` block, immediately before any logging
    or recovery logic. Returns silently when ``exc`` is not critical so
    the caller can continue with its normal error-handling flow.

    An ``ExceptionGroup`` (raised by ``asyncio.TaskGroup`` when a child
    fails) is unwrapped recursively: if any leaf is ``MemoryError`` or
    ``RecursionError`` the group is re-raised so the fatal condition
    propagates before the handler attempts to log.

    Returns silently (no value) when ``exc`` is not, and does not nest,
    ``MemoryError`` or ``RecursionError``, so the caller continues with
    its own logging / cleanup / re-raise logic.

    Args:
        exc: The caught exception. May be any ``BaseException`` instance;
            the helper is permissive about the input type so that a
            future broadening of the surrounding ``except`` clause does
            not silently bypass the re-raise.

    Raises:
        BaseException: Re-raised unchanged when ``exc`` is, or nests, a
            ``MemoryError`` or ``RecursionError``.
    """
    if _contains_critical(exc):
        raise exc
