"""Tests for the backend-agnostic ``RowLike`` row protocol."""

import sqlite3

import pytest

from synthorg.persistence._shared.rows import RowLike


@pytest.mark.unit
class TestRowLike:
    """``RowLike`` structurally matches both backends' row shapes."""

    def test_dict_is_rowlike(self) -> None:
        row: object = {"id": "x"}
        assert isinstance(row, RowLike)

    def test_sqlite_row_is_rowlike(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT 1 AS id").fetchone()
        finally:
            conn.close()
        assert isinstance(row, RowLike)
        assert row["id"] == 1

    def test_object_without_getitem_is_not_rowlike(self) -> None:
        assert not isinstance(object(), RowLike)

    def test_string_key_access_returns_value(self) -> None:
        # A conforming row yields its column value by string key.
        row: RowLike = {"content": "hello"}
        assert row["content"] == "hello"
