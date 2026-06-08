"""Tests for meta/appliers/_validation helpers."""

import pytest

from synthorg.meta.appliers._validation import (
    DottedPathError,
    apply_diff_to_dict,
    parse_dotted_path,
    validate_payload_keys,
)

pytestmark = pytest.mark.unit


class TestParseDottedPath:
    def test_single_segment(self) -> None:
        assert parse_dotted_path("foo") == ("foo",)

    def test_multi_segment(self) -> None:
        assert parse_dotted_path("a.b.c") == ("a", "b", "c")

    def test_blank_rejected(self) -> None:
        with pytest.raises(DottedPathError, match="blank"):
            parse_dotted_path("")

    def test_whitespace_rejected(self) -> None:
        with pytest.raises(DottedPathError, match="blank"):
            parse_dotted_path("   ")

    def test_leading_dot_rejected(self) -> None:
        with pytest.raises(DottedPathError, match="'\\.'"):
            parse_dotted_path(".a.b")

    def test_trailing_dot_rejected(self) -> None:
        with pytest.raises(DottedPathError, match="'\\.'"):
            parse_dotted_path("a.b.")

    def test_double_dot_rejected(self) -> None:
        with pytest.raises(DottedPathError, match="blank"):
            parse_dotted_path("a..b")

    def test_embedded_whitespace_rejected(self) -> None:
        with pytest.raises(DottedPathError, match="blank"):
            parse_dotted_path("a. b")


class TestApplyDiffToDict:
    def test_sets_leaf(self) -> None:
        data: dict[str, object] = {"a": {"b": 1}}
        apply_diff_to_dict(data, path=("a", "b"), new_value=2)
        assert data == {"a": {"b": 2}}

    def test_unknown_leaf_rejected(self) -> None:
        data: dict[str, object] = {"a": {"b": 1}}
        with pytest.raises(DottedPathError, match="unknown"):
            apply_diff_to_dict(data, path=("a", "c"), new_value=2)

    def test_unknown_parent_rejected(self) -> None:
        data: dict[str, object] = {"a": {"b": 1}}
        with pytest.raises(DottedPathError, match="unknown"):
            apply_diff_to_dict(data, path=("x", "b"), new_value=2)

    def test_descend_into_non_dict_rejected(self) -> None:
        data: dict[str, object] = {"a": [1, 2, 3]}
        with pytest.raises(DottedPathError, match="non-dict"):
            apply_diff_to_dict(data, path=("a", "0"), new_value=9)

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(DottedPathError, match="empty"):
            apply_diff_to_dict({"a": 1}, path=(), new_value=2)


class TestValidatePayloadKeys:
    def test_happy_path(self) -> None:
        errors = validate_payload_keys(
            {"description": "hi", "department": "eng"},
            required=frozenset({"description"}),
            allowed=frozenset({"description", "department"}),
        )
        assert errors == []

    def test_missing_required(self) -> None:
        errors = validate_payload_keys(
            {},
            required=frozenset({"description"}),
            allowed=frozenset({"description"}),
        )
        assert errors == ["missing required payload keys: ['description']"]

    def test_unknown_key(self) -> None:
        errors = validate_payload_keys(
            {"description": "hi", "extra": 1},
            required=frozenset({"description"}),
            allowed=frozenset({"description"}),
        )
        assert errors == ["unknown payload keys: ['extra']"]

    def test_none_required_treated_as_missing(self) -> None:
        errors = validate_payload_keys(
            {"description": None},
            required=frozenset({"description"}),
            allowed=frozenset({"description"}),
        )
        assert errors == ["missing required payload keys: ['description']"]
