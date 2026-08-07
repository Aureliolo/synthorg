# module-kind: code
"""Classification of Postgres integrity failures for the whole backend.

An integrity failure is not a query failure. ``QueryError`` is retryable
and surfaces as a 500, which is right for a dropped connection and wrong
for a refused delete: the retry fails identically every time, so a retry
handler burns its whole budget and the operator gets a 500 for what is a
409 condition. :class:`ConstraintViolationError` is the typed answer, and
it lives here rather than inside one repository so every write path
classifies the same way.

Postgres separates the two references a caller cannot tell apart. A plain
foreign-key violation is SQLSTATE 23503; an ``ON DELETE RESTRICT`` refusal
is 23001, a distinct code for the same fact, that a row still points at
what the caller tried to remove. SQLite cannot make that distinction at
all (RESTRICT and a plain reference both surface as ``FOREIGN KEY
constraint failed``), so 23001 is folded onto 23503 here. Without the fold
the same refused delete reaches the API integrity handler as a foreign-key
condition on one backend and as an unrecognised code on the other.
"""

from typing import Final, NoReturn

import psycopg

from synthorg.core.persistence_errors import ConstraintViolationError

_UNKNOWN: Final[str] = "<unknown>"

#: SQL-standard class codes (SQLSTATE). ``restrict_violation`` is reported
#: for a reference declared ``ON DELETE RESTRICT``; ``foreign_key_violation``
#: for every other reference refusal.
SQLSTATE_RESTRICT: Final[str] = "23001"
SQLSTATE_FOREIGN_KEY: Final[str] = "23503"


def constraint_name(exc: psycopg.errors.IntegrityError) -> str:
    """Extract the violated constraint's name from an integrity error.

    Returns:
        The constraint name, or ``"<unknown>"`` when the driver did not
        report one.
    """
    return getattr(getattr(exc, "diag", None), "constraint_name", None) or _UNKNOWN


def shared_sqlstate(exc: psycopg.errors.IntegrityError) -> str | None:
    """Map the driver's SQLSTATE onto the code both backends answer with.

    Args:
        exc: The driver error to read the code from.

    Returns:
        The driver's own code, except a RESTRICT refusal folded onto the
        foreign-key code SQLite reports for the same condition.
    """
    if exc.sqlstate == SQLSTATE_RESTRICT:
        return SQLSTATE_FOREIGN_KEY
    return exc.sqlstate


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
            and the cross-backend SQLSTATE.
    """
    raise ConstraintViolationError(
        message,
        constraint=constraint_name(exc),
        sqlstate=shared_sqlstate(exc),
    ) from exc


__all__ = [
    "SQLSTATE_FOREIGN_KEY",
    "SQLSTATE_RESTRICT",
    "constraint_name",
    "raise_constraint_violation",
    "shared_sqlstate",
]
