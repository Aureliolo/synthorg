"""Tests for ``synthorg.persistence.sqlite._shared``."""

import sqlite3

import pytest

from synthorg.persistence.sqlite._shared import is_unique_constraint_error


@pytest.mark.unit
def test_unique_violation_classified() -> None:
    """A real UNIQUE-constraint failure surfaces as a duplicate."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT UNIQUE)")
    conn.execute("INSERT INTO t (val) VALUES ('a')")
    with pytest.raises(sqlite3.IntegrityError) as info:
        conn.execute("INSERT INTO t (val) VALUES ('a')")
    conn.close()
    assert is_unique_constraint_error(info.value)


@pytest.mark.unit
def test_primary_key_violation_classified() -> None:
    """A real PRIMARY KEY duplicate also surfaces as a duplicate."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (id, val) VALUES (1, 'a')")
    with pytest.raises(sqlite3.IntegrityError) as info:
        conn.execute("INSERT INTO t (id, val) VALUES (1, 'b')")
    conn.close()
    assert is_unique_constraint_error(info.value)


@pytest.mark.unit
def test_check_violation_not_classified() -> None:
    """A CHECK-constraint failure must NOT register as a duplicate."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER, val INTEGER CHECK(val > 0))")
    with pytest.raises(sqlite3.IntegrityError) as info:
        conn.execute("INSERT INTO t (id, val) VALUES (1, -5)")
    conn.close()
    assert not is_unique_constraint_error(info.value)


@pytest.mark.unit
def test_not_null_violation_not_classified() -> None:
    """A NOT NULL failure must NOT register as a duplicate."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER, val TEXT NOT NULL)")
    with pytest.raises(sqlite3.IntegrityError) as info:
        conn.execute("INSERT INTO t (id, val) VALUES (1, NULL)")
    conn.close()
    assert not is_unique_constraint_error(info.value)


@pytest.mark.unit
def test_non_integrity_error_returns_false() -> None:
    """Any non-IntegrityError exception type short-circuits to False."""
    assert not is_unique_constraint_error(ValueError("nope"))
    assert not is_unique_constraint_error(RuntimeError("nope"))
