"""Unit tests for ``_classify_sqlite_integrity`` constraint classification.

Exercises every branch of the SQLite ``IntegrityError`` -> ``(label,
SQLSTATE)`` mapping against real ``sqlite3`` errors, so the extended
result-code classification (preferred over the localised message text)
is covered without the dual-backend conformance harness.
"""

import sqlite3

import pytest

from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.persistence.sqlite.approval_repo import (
    _SQLSTATE_FOREIGN_KEY,
    _SQLSTATE_NOT_NULL,
    _SQLSTATE_UNIQUE,
    _classify_sqlite_integrity,
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
    label, sqlstate = _classify_sqlite_integrity(exc)
    assert sqlstate == _SQLSTATE_UNIQUE
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
    _label, sqlstate = _classify_sqlite_integrity(exc)
    assert sqlstate == _SQLSTATE_UNIQUE


@pytest.mark.unit
def test_not_null_violation_maps_to_23502() -> None:
    exc = _raise(
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT NOT NULL)",),
        "INSERT INTO t (val) VALUES (NULL)",
    )
    label, sqlstate = _classify_sqlite_integrity(exc)
    assert sqlstate == _SQLSTATE_NOT_NULL
    assert "val" in label


@pytest.mark.unit
def test_foreign_key_violation_maps_to_23503() -> None:
    exc = _raise(
        (
            "CREATE TABLE parent (id INTEGER PRIMARY KEY)",
            "CREATE TABLE child (id INTEGER PRIMARY KEY, "
            "parent_id INTEGER REFERENCES parent(id))",
        ),
        "INSERT INTO child (id, parent_id) VALUES (1, 999)",
    )
    label, sqlstate = _classify_sqlite_integrity(exc)
    assert sqlstate == _SQLSTATE_FOREIGN_KEY
    assert label == "foreign_key"


@pytest.mark.unit
def test_check_violation_maps_to_check_constraint_no_sqlstate() -> None:
    exc = _raise(
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER CHECK (n > 0))",),
        "INSERT INTO t (n) VALUES (0)",
    )
    label, sqlstate = _classify_sqlite_integrity(exc)
    assert label == "check_constraint"
    assert sqlstate is None


@pytest.mark.unit
def test_unknown_constraint_falls_back() -> None:
    # A non-constraint IntegrityError (no recognised extended code or
    # message kind) degrades to the UNKNOWN_CONSTRAINT label.
    exc = sqlite3.IntegrityError("some unrecognised integrity failure")
    label, sqlstate = _classify_sqlite_integrity(exc)
    assert label == ConstraintViolationError.UNKNOWN_CONSTRAINT
    assert sqlstate is None
