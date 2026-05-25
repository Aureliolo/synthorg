"""Shared helpers for the SQLite persistence backend."""

import sqlite3
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager


class WriteContext(Protocol):
    """Factory of a one-shot async context manager that serializes writes.

    Repositories store one of these and call
    ``async with self._write_context()`` around every multi-statement
    transaction. The backend wires its own ``write_context`` bound method
    as the factory, so each call returns a fresh context manager whose
    underlying lock is shared across every repo on the connection.

    Modelled as a ``Protocol`` (rather than ``Callable[[], CM[None]]``)
    so the name carries the intent (serializing-writes factory) at the
    type level, not just the shape.
    """

    def __call__(self) -> AbstractAsyncContextManager[None]: ...


def is_unique_constraint_error(exc: BaseException) -> bool:
    """Return ``True`` for SQLite UNIQUE / PRIMARY KEY violations.

    Uses ``sqlite_errorname`` (Python 3.11+) on the underlying
    ``sqlite3.IntegrityError``, which is the authoritative signal:
    SQLite reports ``SQLITE_CONSTRAINT_UNIQUE`` for ``UNIQUE`` failures
    and ``SQLITE_CONSTRAINT_PRIMARYKEY`` for ``PRIMARY KEY`` duplicates.
    The project targets Python 3.14+, so the attribute is always
    present.

    Accepts ``BaseException`` so the helper can be passed directly to
    callers that classify any error type (e.g. the
    ``IsDuplicate`` protocol in ``synthorg.persistence._shared.audit``)
    -- non-``IntegrityError`` exceptions short-circuit to ``False``.

    Substring matching on the error message (the historical pattern in
    some repos) is brittle: the message format includes column names
    that vary per table, breaks for SQLite builds with localised
    messages, and silently classifies CHECK / FOREIGN KEY violations
    as duplicates when their messages happen to start with "UNIQUE
    constraint" in some upstream patches.

    Returns:
        ``True`` when ``exc`` is a SQLite UNIQUE or PRIMARY KEY violation, ``False``
        otherwise.
    """
    if not isinstance(exc, sqlite3.IntegrityError):
        return False
    return exc.sqlite_errorname in {
        "SQLITE_CONSTRAINT_UNIQUE",
        "SQLITE_CONSTRAINT_PRIMARYKEY",
    }
