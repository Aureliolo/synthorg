# module-kind: code
"""Classification of Postgres integrity failures for the whole backend.

An integrity failure is not a query failure. ``QueryError`` is retryable
and surfaces as a 500, which is right for a dropped connection and wrong
for a refused delete: the retry fails identically every time, so a retry
handler burns its whole budget and the operator gets a 500 for what is a
409 condition. :class:`ConstraintViolationError` is the typed answer, and
it lives here rather than inside one repository so every write path
classifies the same way.

Postgres reports both a plain foreign-key violation and an
``ON DELETE RESTRICT`` refusal as SQLSTATE 23503, which is the shape the
SQLite side is mapped onto so both backends answer a caller identically.
"""

from typing import Final, NoReturn

import psycopg

from synthorg.core.persistence_errors import ConstraintViolationError

_UNKNOWN: Final[str] = "<unknown>"


def constraint_name(exc: psycopg.errors.IntegrityError) -> str:
    """Extract the violated constraint's name from an integrity error.

    Returns:
        The constraint name, or ``"<unknown>"`` when the driver did not
        report one.
    """
    return getattr(getattr(exc, "diag", None), "constraint_name", None) or _UNKNOWN


def raise_constraint_violation(
    exc: psycopg.errors.IntegrityError, message: str
) -> NoReturn:
    """Re-raise an integrity failure as the typed, non-retryable error.

    Args:
        exc: The driver error to classify. Chained as the cause, so the
            driver's own message survives for a reader of the traceback.
        message: What the caller was doing, for the operator.

    Raises:
        ConstraintViolationError: Always, carrying the constraint name
            and the driver's SQLSTATE.
    """
    raise ConstraintViolationError(
        message,
        constraint=constraint_name(exc),
        sqlstate=exc.sqlstate,
    ) from exc


__all__ = ["constraint_name", "raise_constraint_violation"]
