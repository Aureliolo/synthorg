"""Unit tests for the role-registry JSON ``_str_tuple`` decoders.

Both backends store the role's ``required_skills`` / ``tool_access`` tuples
as JSON arrays. The decoders must reject corrupt persisted payloads (a JSON
string, object, or array with non-string / blank entries) instead of
silently coercing them into a wrong tuple (e.g. a JSON string iterated into
per-character entries).
"""

import json

import pytest

from synthorg.persistence.postgres.role_registry_repo import (
    _str_tuple as pg_str_tuple,
)
from synthorg.persistence.sqlite.role_registry_repo import (
    _str_tuple as sqlite_str_tuple,
)

pytestmark = pytest.mark.unit


class TestSqliteStrTuple:
    def test_valid_array_round_trips(self) -> None:
        assert sqlite_str_tuple(json.dumps(["python", "go"])) == ("python", "go")

    def test_empty_array_is_empty_tuple(self) -> None:
        assert sqlite_str_tuple(json.dumps([])) == ()

    def test_json_string_is_rejected(self) -> None:
        # Without the guard this would iterate into ("p", "y", ...).
        with pytest.raises(TypeError):
            sqlite_str_tuple(json.dumps("python"))

    def test_json_object_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            sqlite_str_tuple(json.dumps({"skill": "python"}))

    def test_non_string_element_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank strings"):
            sqlite_str_tuple(json.dumps(["python", 3]))

    def test_blank_element_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank strings"):
            sqlite_str_tuple(json.dumps(["python", "  "]))


class TestPostgresStrTuple:
    def test_valid_list_round_trips(self) -> None:
        assert pg_str_tuple(["python", "go"]) == ("python", "go")

    def test_valid_json_string_array_round_trips(self) -> None:
        assert pg_str_tuple(json.dumps(["python"])) == ("python",)

    def test_non_array_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            pg_str_tuple(json.dumps("python"))

    def test_non_string_element_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank strings"):
            pg_str_tuple(["python", 3])

    def test_blank_element_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-blank strings"):
            pg_str_tuple(["python", ""])
