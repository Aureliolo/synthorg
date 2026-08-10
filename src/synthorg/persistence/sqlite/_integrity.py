# module-kind: code
"""Classification of SQLite integrity failures for the whole backend.

An integrity failure is not a query failure. ``QueryError`` is retryable
and surfaces as a 500, which is right for a dropped connection and wrong
for a refused delete: the retry fails identically every time, so a retry
handler burns its whole budget and the operator gets a 500 for what is a
409 condition. :class:`ConstraintViolationError` is the typed answer, and
it lives here rather than inside one repository so every write path
classifies the same way.

Measured on the ``plans.parent_task_id`` reference this backend now
carries: SQLite reports an ``ON DELETE RESTRICT`` refusal with the
extended code ``SQLITE_CONSTRAINT_TRIGGER`` (1811), not
``SQLITE_CONSTRAINT_FOREIGNKEY`` (787), because RESTRICT is implemented
as an internal trigger program. Both carry the message ``FOREIGN KEY
constraint failed``, so the message pass below is what catches the
RESTRICT case, and it is deliberately reached rather than short-circuited
by an 1811 branch: a genuine user trigger raising its own message is also
1811 and is not a foreign-key violation.
"""

import sqlite3
from typing import Final, NoReturn

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.persistence_errors import (
    SQLSTATE_FOREIGN_KEY,
    SQLSTATE_NOT_NULL,
    SQLSTATE_UNIQUE,
    ConstraintViolationError,
)

# SQLite does not emit SQLSTATE, so its integrity-failure messages are mapped
# onto the shared codes in ``core.persistence_errors`` and both backends
# classify a violation identically.

_FOREIGN_KEY_LABEL: Final[str] = "foreign_key"
_CHECK_LABEL: Final[str] = "check_constraint"


def classify_sqlite_integrity(exc: sqlite3.IntegrityError) -> tuple[str, str | None]:
    """Map a SQLite ``IntegrityError`` to a stable label + SQLSTATE.

    Returns a stable constraint token (``table.column`` for unique /
    not-null, a fixed label for foreign-key / check) rather than the raw
    message, so a CHECK-constraint expression never leaks into the
    surfaced ``constraint`` attribute, plus the Postgres-equivalent
    SQLSTATE (``None`` when the failure does not map to a branch the API
    handler distinguishes).

    Returns:
        ``(constraint_label, sqlstate)``.
    """
    head, _, target = str(exc).partition(":")
    label = target.strip() or ConstraintViolationError.UNKNOWN_CONSTRAINT

    # Prefer the extended result code: it classifies the violation kind
    # reliably regardless of the SQLite build's localised message text,
    # which the string parse below depends on. The message is still the
    # only source for the ``table.column`` label, so both are used. A
    # PRIMARY KEY clash is a uniqueness violation and maps to 23505.
    # ``getattr`` (not direct access) so an IntegrityError without the
    # attribute -- a non-driver-originated one -- degrades to the
    # message-string fallback below instead of raising.
    ext_code = getattr(exc, "sqlite_errorcode", None)
    if ext_code in (
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    ):
        return label, SQLSTATE_UNIQUE
    if ext_code == sqlite3.SQLITE_CONSTRAINT_NOTNULL:
        return label, SQLSTATE_NOT_NULL
    if ext_code == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY:
        return _FOREIGN_KEY_LABEL, SQLSTATE_FOREIGN_KEY
    if ext_code == sqlite3.SQLITE_CONSTRAINT_CHECK:
        return _CHECK_LABEL, None

    kind = normalize_ascii_lowercase(head)
    if kind == "unique constraint failed":
        return label, SQLSTATE_UNIQUE
    if kind == "not null constraint failed":
        return label, SQLSTATE_NOT_NULL
    if kind == "foreign key constraint failed":
        return _FOREIGN_KEY_LABEL, SQLSTATE_FOREIGN_KEY
    if kind == "check constraint failed":
        return _CHECK_LABEL, None
    return ConstraintViolationError.UNKNOWN_CONSTRAINT, None


def raise_constraint_violation(exc: sqlite3.IntegrityError, message: str) -> NoReturn:
    """Re-raise an integrity failure as the typed, non-retryable error.

    Args:
        exc: The driver error to classify. Chained as the cause, so the
            driver's own message survives for a reader of the traceback.
        message: What the caller was doing, for the operator.

    Raises:
        ConstraintViolationError: Always, carrying the constraint label
            and the equivalent SQLSTATE.
    """
    constraint, sqlstate = classify_sqlite_integrity(exc)
    raise ConstraintViolationError(
        message, constraint=constraint, sqlstate=sqlstate
    ) from exc


__all__ = [
    "SQLSTATE_FOREIGN_KEY",
    "SQLSTATE_NOT_NULL",
    "SQLSTATE_UNIQUE",
    "classify_sqlite_integrity",
    "raise_constraint_violation",
]
