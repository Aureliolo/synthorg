"""Unit tests for SQLite integrity-failure classification.

Exercises every branch of the SQLite ``IntegrityError`` -> ``(label,
SQLSTATE)`` mapping against real ``sqlite3`` errors, so the extended
result-code classification (preferred over the localised message text)
is covered without the dual-backend conformance harness.
"""

import sqlite3

import pytest

from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.persistence.sqlite._integrity import (
    SQLSTATE_FOREIGN_KEY,
    SQLSTATE_NOT_NULL,
    SQLSTATE_UNIQUE,
    classify_sqlite_integrity,
    raise_constraint_violation,
)


def _raise(setup: tuple[str, ...], offending: str) -> sqlite3.IntegrityError:
    """Run ``setup`` then ``offending`` and return the raised IntegrityError.

    Returns:
        The ``sqlite3.IntegrityError`` raised by the offending statement.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        for stmt in setup:
            conn.execute(stmt)
        with pytest.raises(sqlite3.IntegrityError) as info:
            conn.execute(offending)
    finally:
        conn.close()
    return info.value


@pytest.mark.unit
def test_unique_violation_maps_to_23505() -> None:
    exc = _raise(
        (
            "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT UNIQUE)",
            "INSERT INTO t (val) VALUES ('a')",
        ),
        "INSERT INTO t (val) VALUES ('a')",
    )
    label, sqlstate = classify_sqlite_integrity(exc)
    assert sqlstate == SQLSTATE_UNIQUE
    assert "val" in label


@pytest.mark.unit
def test_primary_key_violation_maps_to_23505() -> None:
    # A PRIMARY KEY clash is a uniqueness violation and must share 23505.
    exc = _raise(
        (
            "CREATE TABLE t (id INTEGER PRIMARY KEY)",
            "INSERT INTO t (id) VALUES (1)",
        ),
        "INSERT INTO t (id) VALUES (1)",
    )
    _label, sqlstate = classify_sqlite_integrity(exc)
    assert sqlstate == SQLSTATE_UNIQUE


@pytest.mark.unit
def test_not_null_violation_maps_to_23502() -> None:
    exc = _raise(
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT NOT NULL)",),
        "INSERT INTO t (val) VALUES (NULL)",
    )
    label, sqlstate = classify_sqlite_integrity(exc)
    assert sqlstate == SQLSTATE_NOT_NULL
    assert "val" in label


@pytest.mark.unit
def test_foreign_key_violation_maps_to_23503() -> None:
    exc = _raise(
        (
            "CREATE TABLE parent (id INTEGER PRIMARY KEY)",
            (
                "CREATE TABLE child (id INTEGER PRIMARY KEY, "
                "parent_id INTEGER REFERENCES parent(id))"
            ),
        ),
        "INSERT INTO child (id, parent_id) VALUES (1, 999)",
    )
    label, sqlstate = classify_sqlite_integrity(exc)
    assert sqlstate == SQLSTATE_FOREIGN_KEY
    assert label == "foreign_key"


@pytest.mark.unit
def test_a_restrict_refusal_maps_to_23503_despite_its_trigger_code() -> None:
    """SQLite implements RESTRICT as a trigger, so the code is 1811, not 787.

    A classifier reading only the extended code would miss exactly the
    refusal ``plans.parent_task_id`` exists to produce, and the caller
    would get a retryable 500 for a permanent 409 condition.
    """
    exc = _raise(
        (
            "CREATE TABLE tasks (id TEXT PRIMARY KEY)",
            (
                "CREATE TABLE plans (id TEXT PRIMARY KEY, parent_task_id TEXT "
                "NOT NULL REFERENCES tasks (id) ON DELETE RESTRICT)"
            ),
            "INSERT INTO tasks VALUES ('t1')",
            "INSERT INTO plans VALUES ('p1', 't1')",
        ),
        "DELETE FROM tasks WHERE id = 't1'",
    )

    assert exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
    label, sqlstate = classify_sqlite_integrity(exc)
    assert (label, sqlstate) == ("foreign_key", SQLSTATE_FOREIGN_KEY)


@pytest.mark.unit
def test_the_typed_error_carries_the_classification() -> None:
    """The caller branches on ``constraint`` / ``sqlstate``, not on prose."""
    exc = _raise(
        (
            "CREATE TABLE parent (id INTEGER PRIMARY KEY)",
            (
                "CREATE TABLE child (id INTEGER PRIMARY KEY, "
                "parent_id INTEGER REFERENCES parent(id))"
            ),
        ),
        "INSERT INTO child (id, parent_id) VALUES (1, 999)",
    )

    with pytest.raises(ConstraintViolationError) as info:
        raise_constraint_violation(exc, "Failed to do the thing")

    assert info.value.constraint == "foreign_key"
    assert info.value.sqlstate == SQLSTATE_FOREIGN_KEY
    assert info.value.is_retryable is False
    assert info.value.__cause__ is exc


@pytest.mark.unit
def test_check_violation_maps_to_check_constraint_no_sqlstate() -> None:
    exc = _raise(
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER CHECK (n > 0))",),
        "INSERT INTO t (n) VALUES (0)",
    )
    label, sqlstate = classify_sqlite_integrity(exc)
    assert label == "check_constraint"
    assert sqlstate is None


@pytest.mark.unit
def test_unknown_constraint_falls_back() -> None:
    # A non-constraint IntegrityError (no recognised extended code or
    # message kind) degrades to the UNKNOWN_CONSTRAINT label.
    exc = sqlite3.IntegrityError("some unrecognised integrity failure")
    label, sqlstate = classify_sqlite_integrity(exc)
    assert label == ConstraintViolationError.UNKNOWN_CONSTRAINT
    assert sqlstate is None
